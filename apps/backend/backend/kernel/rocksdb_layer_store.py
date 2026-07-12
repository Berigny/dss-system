"""RocksDB-backed geological layer store.

Persists ledger entries under layer-prefixed keys following the v1.3-alpha
schema:

    Key:   {layer}:{coord}:{block_height}:{hash_prefix}
    Value: {v6}:{v7}:{v8}:{merkle_path}:{zk_proof_stub}:{timestamp}

Layer prefixes:
    S: Sand   (high-velocity input buffer, evicted after one block)
    I: Silt   (preliminary sorting buffer, decayed Loam)
    L: Loam   (fertile pending, logarithmic decay)
    C: Clay   (permanent identity substrate, immutable)
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from threading import RLock
from typing import Any, Mapping, MutableMapping

from backend.kernel import constants
from backend.kernel.base_foundation import BaseFoundationService
from backend.kernel.layer_router import LayerRouter
from backend.kernel.quaternary_gates import QuaternaryGate


class RocksDBLayerStore:
    """Store and route entries across Sand/Silt/Loam/Clay namespaces."""

    PREFIX_MAP: Mapping[str, str] = {
        constants.LAYER_SAND: "S",
        constants.LAYER_SILT: "I",
        constants.LAYER_LOAM: "L",
        constants.LAYER_CLAY: "C",
    }

    SAND_RETENTION_BLOCKS: int = 1

    def __init__(
        self,
        db: MutableMapping[bytes, bytes],
        *,
        layer_router: LayerRouter | None = None,
        provision_id: str = "default",
    ) -> None:
        self._db = db
        self._router = layer_router or LayerRouter()
        self._provision_id = provision_id
        self._lock = RLock()

    @classmethod
    def _layer_prefix(cls, layer: str) -> str:
        return cls.PREFIX_MAP[layer]

    @staticmethod
    def _normalize_coord(coord: str) -> str:
        """Normalize a COORD for use in a RocksDB key.

        Normalization is lowercase, slash-separated, with spaces collapsed to
        underscores and leading/trailing slashes removed.
        """
        normalized = str(coord).lower().strip()
        normalized = normalized.replace(" ", "_")
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        normalized = normalized.strip("/")
        return normalized

    @classmethod
    def _make_key(
        cls,
        layer: str,
        coord: str,
        block_height: int,
        hash_prefix: str,
    ) -> bytes:
        prefix = cls._layer_prefix(layer)
        normalized_coord = cls._normalize_coord(coord)
        return f"{prefix}:{normalized_coord}:{block_height}:{hash_prefix}".encode("utf-8")

    @staticmethod
    def _sanitize_field(value: str) -> str:
        """Remove colons from a value field so the colon schema stays unambiguous."""
        return str(value).replace(":", "_")

    @staticmethod
    def _make_value(
        v_awareness: int,
        v_unity: int,
        v_ethics: int,
        merkle_path: str,
        zk_proof_stub: str,
        timestamp: float,
        elevation_bundle: dict[str, Any] | None,
    ) -> bytes:
        """Serialize to the v1.3-alpha value schema.

        Core fields are colon-delimited:
            {v6}:{v7}:{v8}:{merkle_path}:{zk_proof_stub}:{timestamp}
        An optional ``elevation_bundle`` is appended after a ``|`` separator so
        HENGE-005 hysteresis proofs are still persisted.
        """
        core = ":".join(
            [
                str(int(v_awareness)),
                str(int(v_unity)),
                str(int(v_ethics)),
                RocksDBLayerStore._sanitize_field(merkle_path),
                RocksDBLayerStore._sanitize_field(zk_proof_stub),
                str(float(timestamp)),
            ]
        )
        if elevation_bundle is not None:
            bundle_json = json.dumps(
                elevation_bundle, separators=(",", ":"), sort_keys=True
            ).replace("|", "\\|")
            core = f"{core}|{bundle_json}"
        return core.encode("utf-8")

    @staticmethod
    def _decode_value(raw: bytes) -> dict[str, Any]:
        """Parse the colon-delimited value schema back into a dict."""
        text = raw.decode("utf-8")
        core_text, _, bundle_text = text.partition("|")
        parts = core_text.split(":")
        if len(parts) < 6:
            raise ValueError(f"Invalid layer-store value: {text!r}")
        result: dict[str, Any] = {
            "v_awareness": int(parts[0]),
            "v_unity": int(parts[1]),
            "v_ethics": int(parts[2]),
            "merkle_path": parts[3],
            "zk_proof_stub": parts[4],
            "timestamp": float(parts[5]),
        }
        if bundle_text:
            result["elevation_bundle"] = json.loads(bundle_text.replace("\\|", "|"))
        return result

    @staticmethod
    def _hash_prefix(data: str) -> str:
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]

    def _next_block_height(self, proposed: int | None) -> int:
        """Return the next valid block height for this provision namespace.

        Heights are monotonically increasing. A missing proposal auto-increments
        from the last stored height; an explicit proposal is accepted only if it
        is not lower than the last stored height.
        """
        last = int(self._read_quaternary_state().get("last_block_height", 0))
        if proposed is None:
            return last + 1
        proposed_int = int(proposed)
        if proposed_int < last:
            raise ValueError(
                f"block height must be monotonic: proposed {proposed_int} < last {last}"
            )
        return proposed_int

    def write(self, entry: Mapping[str, Any]) -> str:
        """Route ``entry`` to its geological layer and persist it.

        An explicit ``layer`` field in ``entry`` overrides the router, but
        Clay writes are still validated against the quaternary levels.

        Raises:
            ValueError: if the routed layer is Clay but the entry does not
                satisfy the Level-3 / 336-checksum requirement.
        """
        layer = entry.get("layer") or self._router.route(entry)
        v_awareness = int(entry.get("v_awareness", 0))
        v_unity = int(entry.get("v_unity", 0))
        v_ethics = int(entry.get("v_ethics", 0))

        if layer == constants.LAYER_CLAY:
            evaluation = QuaternaryGate.evaluate(v_awareness, v_unity, v_ethics)
            if not evaluation["clay_admissible"]:
                raise ValueError(
                    "Clay write rejected: all gates must be Level 3 and the "
                    "336 checksum must be satisfied."
                )

        coord = str(entry.get("coord", "unknown"))
        block_height = self._next_block_height(entry.get("block_height"))
        hash_prefix = str(entry.get("hash_prefix")) or self._hash_prefix(coord)
        merkle_path = str(entry.get("merkle_path", ""))
        zk_proof_stub = str(entry.get("zk_proof_stub", ""))
        timestamp = float(entry.get("timestamp", time.time()))

        key = self._make_key(layer, coord, block_height, hash_prefix)
        elevation_bundle = entry.get("elevation_bundle")
        value = self._make_value(
            v_awareness, v_unity, v_ethics, merkle_path, zk_proof_stub, timestamp, elevation_bundle
        )

        with self._lock:
            self._db[key] = value
            self._update_foundation_state(
                layer,
                v_awareness,
                v_unity,
                v_ethics,
                block_height,
            )
        return layer

    def _read_quaternary_state(self) -> dict[str, Any]:
        """Return the current quaternary state from the base foundation."""
        service = BaseFoundationService(self._db)
        record = service.read_foundation(self._provision_id) or {}
        public = record.get("public") or {}
        checksum = public.get("checksum_336") or {"name": "checksum_336", "value": 336}
        return checksum.get("quaternary_state") or {
            "layer_counts": {layer: 0 for layer in constants.QUATERNARY_LAYER_ORDER},
            "last_block_height": 0,
            "last_checksum_factor_product": 0.0,
        }

    def _write_quaternary_state(self, quaternary_state: dict[str, Any]) -> None:
        """Persist the quaternary state back to the base foundation."""
        service = BaseFoundationService(self._db)
        record = service.read_foundation(self._provision_id) or {}
        public = record.get("public") or {}
        checksum = public.get("checksum_336") or {"name": "checksum_336", "value": 336}
        checksum["quaternary_state"] = quaternary_state
        public["checksum_336"] = checksum
        record["public"] = public
        service._db[service._foundation_key(self._provision_id)] = json.dumps(
            record, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    def _update_foundation_state(
        self,
        layer: str,
        v_awareness: int,
        v_unity: int,
        v_ethics: int,
        block_height: int,
    ) -> None:
        """Atomically update the base foundation checksum state on write."""
        quaternary_state = self._read_quaternary_state()
        evaluation = QuaternaryGate.evaluate(v_awareness, v_unity, v_ethics)
        counts = dict(quaternary_state["layer_counts"])
        counts[layer] = counts.get(layer, 0) + 1

        quaternary_state.update(
            {
                "layer_counts": counts,
                "last_block_height": max(quaternary_state.get("last_block_height", 0), block_height),
                "last_checksum_factor_product": evaluation["checksum_factor_product"],
                "last_evaluated": {
                    "v_awareness": v_awareness,
                    "v_unity": v_unity,
                    "v_ethics": v_ethics,
                    "levels": evaluation["levels"],
                },
            }
        )
        self._write_quaternary_state(quaternary_state)

    def _migrate_foundation_counts(
        self,
        from_layer: str,
        to_layer: str,
        block_height: int,
    ) -> None:
        """Atomically update layer counts during a layer migration."""
        quaternary_state = self._read_quaternary_state()
        counts = dict(quaternary_state["layer_counts"])
        counts[from_layer] = max(counts.get(from_layer, 0) - 1, 0)
        counts[to_layer] = counts.get(to_layer, 0) + 1
        quaternary_state.update(
            {
                "layer_counts": counts,
                "last_block_height": max(quaternary_state.get("last_block_height", 0), block_height),
            }
        )
        self._write_quaternary_state(quaternary_state)

    def read(
        self,
        layer: str,
        coord: str,
        block_height: int,
        hash_prefix: str,
    ) -> dict[str, Any] | None:
        """Return the decoded entry at the exact key, or ``None``."""
        key = self._make_key(layer, coord, block_height, hash_prefix)
        raw = self._db.get(key)
        if raw is None:
            return None
        return self._decode_value(raw)

    def retrieve_by_coord(self, coord: str) -> list[tuple[str, int, dict[str, Any]]]:
        """Return all entries indexed under ``coord`` without scanning values.

        Only key strings are inspected; raw entry values are never read during
        the search. Results are ``(layer, block_height, decoded_entry)`` tuples.
        """
        normalized = self._normalize_coord(coord)
        results: list[tuple[str, int, dict[str, Any]]] = []
        for key in self._db.keys():
            if not isinstance(key, bytes):
                continue
            try:
                text = key.decode("utf-8")
            except UnicodeDecodeError:
                continue
            parts = text.split(":")
            if len(parts) < 4:
                continue
            if parts[1] == normalized:
                layer = next(
                    (l for l, p in self.PREFIX_MAP.items() if p == parts[0]),
                    parts[0],
                )
                block_height = int(parts[2])
                raw = self._db[key]
                results.append((layer, block_height, self._decode_value(raw)))
        return results

    def list_layer(self, layer: str) -> list[tuple[bytes, dict[str, Any]]]:
        """Return all entries stored under ``layer`` as ``(key, entry)`` pairs."""
        prefix = self._layer_prefix(layer).encode("utf-8") + b":"
        results: list[tuple[bytes, dict[str, Any]]] = []
        for key, raw in self._db.items():
            if isinstance(key, bytes) and key.startswith(prefix):
                results.append((key, self._decode_value(raw)))
        return results
        results: list[tuple[bytes, dict[str, Any]]] = []
        for key, raw in self._db.items():
            if isinstance(key, bytes) and key.startswith(prefix):
                results.append((key, self._decode_value(raw)))
        return results

    def decay_loam(self, current_block: int) -> list[dict[str, Any]]:
        """Decay Loam valuations and migrate zero-valued entries to Silt.

        Returns:
            A list of migration records for entries that moved to Silt.
        """
        migrations: list[dict[str, Any]] = []
        prefix = self._layer_prefix(constants.LAYER_LOAM).encode("utf-8") + b":"

        with self._lock:
            for key, raw in list(self._db.items()):
                if not (isinstance(key, bytes) and key.startswith(prefix)):
                    continue
                entry = self._decode_value(raw)
                written_block = int(key.split(b":")[2])
                blocks_elapsed = max(current_block - written_block, 0)
                if blocks_elapsed <= 0:
                    continue

                delta = math.log2(blocks_elapsed)
                new_awareness = max(entry["v_awareness"] - delta, 0.0)
                new_unity = max(entry["v_unity"] - delta, 0.0)
                new_ethics = max(entry["v_ethics"] - delta, 0.0)

                if new_awareness == 0 and new_unity == 0 and new_ethics == 0:
                    # Migrate to Silt.
                    del self._db[key]
                    parts = key.decode("utf-8").split(":")
                    silt_key = self._make_key(
                        constants.LAYER_SILT,
                        parts[1],
                        current_block,
                        parts[3],
                    )
                    entry["v_awareness"] = 0
                    entry["v_unity"] = 0
                    entry["v_ethics"] = 0
                    entry["migrated_from"] = constants.LAYER_LOAM
                    self._db[silt_key] = self._make_value(
                        entry["v_awareness"],
                        entry["v_unity"],
                        entry["v_ethics"],
                        entry.get("merkle_path", ""),
                        entry.get("zk_proof_stub", ""),
                        entry.get("timestamp", time.time()),
                        entry.get("elevation_bundle"),
                    )
                    self._migrate_foundation_counts(
                        constants.LAYER_LOAM, constants.LAYER_SILT, current_block
                    )
                    migrations.append(
                        {
                            "from": constants.LAYER_LOAM,
                            "to": constants.LAYER_SILT,
                            "coord": parts[1],
                            "block_height": current_block,
                        }
                    )
                else:
                    entry["v_awareness"] = new_awareness
                    entry["v_unity"] = new_unity
                    entry["v_ethics"] = new_ethics
                    self._db[key] = self._make_value(
                        entry["v_awareness"],
                        entry["v_unity"],
                        entry["v_ethics"],
                        entry.get("merkle_path", ""),
                        entry.get("zk_proof_stub", ""),
                        entry.get("timestamp", time.time()),
                        entry.get("elevation_bundle"),
                    )
        return migrations

    def evict_sand(self, current_block: int) -> list[str]:
        """Remove Sand entries written before ``current_block - 1``.

        Returns:
            A list of evicted coordinate strings.
        """
        evicted: list[str] = []
        prefix = self._layer_prefix(constants.LAYER_SAND).encode("utf-8") + b":"

        with self._lock:
            for key in list(self._db.keys()):
                if not (isinstance(key, bytes) and key.startswith(prefix)):
                    continue
                written_block = int(key.split(b":")[2])
                if current_block - written_block > self.SAND_RETENTION_BLOCKS:
                    del self._db[key]
                    parts = key.decode("utf-8").split(":")
                    evicted.append(parts[1])
        return evicted

    def clay_ledger(self) -> dict[str, dict[str, Any]]:
        """Return the current Clay layer as a coord -> state mapping."""
        ledger: dict[str, dict[str, Any]] = {}
        for key, entry in self.list_layer(constants.LAYER_CLAY):
            parts = key.decode("utf-8").split(":")
            coord = parts[1]
            block_height = int(parts[2])
            ledger[coord] = {
                "v_values": [
                    int(entry["v_awareness"]),
                    int(entry["v_unity"]),
                    int(entry["v_ethics"]),
                ],
                "block_height": block_height,
                "value": entry.get("value", ""),
            }
        return ledger

    def _origin_salt(self) -> int:
        """Return the provision's unique origin salt, or 0 if none exists."""
        foundation = BaseFoundationService(self._db).read_foundation(
            self._provision_id
        )
        if not isinstance(foundation, dict):
            return 0
        public = foundation.get("public")
        if not isinstance(public, dict):
            return 0
        salt = public.get("origin_salt", 0)
        if isinstance(salt, int):
            return salt
        if isinstance(salt, str):
            try:
                return int(salt, 16)
            except ValueError:
                return 0
        return 0

    def clay_merkle_root(self) -> int:
        """Return the Merkle root of the current Clay ledger."""
        from backend.kernel.merkle_poseidon import MerkleTree, leaf_hash

        ledger = self.clay_ledger()
        if not ledger:
            return 0
        salt = self._origin_salt()
        leaves = [
            leaf_hash(coord, state["v_values"], salt=salt)
            for coord, state in sorted(ledger.items())
        ]
        return MerkleTree(leaves).root
