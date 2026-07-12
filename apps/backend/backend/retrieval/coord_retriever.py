"""COORD branch navigation and hysteresis retrieval.

Retrieval operates on RocksDB layer-store keys only; no full-text index is used.
"""

from __future__ import annotations

import math
from typing import Any

from backend.kernel import constants
from backend.kernel.rocksdb_layer_store import RocksDBLayerStore


class CoordRetriever:
    """Retrieve ledger entries by COORD branch navigation and hysteresis."""

    def __init__(self, store: RocksDBLayerStore) -> None:
        self._store = store

    @staticmethod
    def _coord_segments(coord: str) -> list[str]:
        return [segment for segment in coord.split("/") if segment]

    def retrieve_exact(self, coord: str) -> list[dict[str, Any]]:
        """Return all entries stored under the exact COORD ``coord``.

        This is the primary COORD lookup; only keys are inspected during the
        search. Values are decoded only for matching keys.
        """
        results = self._store.retrieve_by_coord(coord)
        return [
            {
                "layer": layer,
                "block_height": block_height,
                "coord": self._store._normalize_coord(coord),
                "entry": entry,
            }
            for layer, block_height, entry in results
        ]

    def expand_branch(
        self,
        coord: str,
        *,
        max_depth: int = 2,
    ) -> list[dict[str, Any]]:
        """Expand ``coord`` to siblings sharing the same parent branch.

        ``max_depth`` controls how many leading segments define the shared
        parent. For example, with ``coord == "ethics/lawfulness/refusal/v3"``
        and ``max_depth == 2``, all COORDs under ``ethics/lawfulness`` are
        returned.
        """
        segments = self._coord_segments(coord)
        depth = max(1, min(max_depth, len(segments)))
        parent_prefix = "/".join(segments[:depth])
        normalized_parent = self._store._normalize_coord(parent_prefix)
        parent_parts = len(self._coord_segments(normalized_parent))

        results: list[dict[str, Any]] = []
        for key in self._store._db.keys():
            if not isinstance(key, bytes):
                continue
            try:
                text = key.decode("utf-8")
            except UnicodeDecodeError:
                continue
            parts = text.split(":")
            if len(parts) < 4:
                continue
            key_coord = parts[1]
            key_segments = self._coord_segments(key_coord)
            if len(key_segments) < parent_parts:
                continue
            if "/".join(key_segments[:parent_parts]) == normalized_parent:
                layer = next(
                    (l for l, p in self._store.PREFIX_MAP.items() if p == parts[0]),
                    parts[0],
                )
                block_height = int(parts[2])
                entry = self._store._decode_value(self._store._db[key])
                results.append(
                    {
                        "layer": layer,
                        "block_height": block_height,
                        "coord": key_coord,
                        "entry": entry,
                    }
                )
        return results

    def hysteresis_ancestors(
        self,
        clay_coord: str,
        clay_block_height: int,
    ) -> list[dict[str, Any]]:
        """Return Loam ancestors of ``clay_coord`` with decayed v-values.

        Only Loam entries whose COORD is a prefix of ``clay_coord`` and whose
        block height is at or before ``clay_block_height`` are considered.
        Values are decayed by ``log2(blocks_elapsed)`` as defined in the
        v1.3-alpha spec.
        """
        normalized_clay = self._store._normalize_coord(clay_coord)
        clay_segments = self._coord_segments(normalized_clay)
        loam_prefix = self._store.PREFIX_MAP[constants.LAYER_LOAM].encode("utf-8") + b":"
        results: list[dict[str, Any]] = []

        for key in self._store._db.keys():
            if not (isinstance(key, bytes) and key.startswith(loam_prefix)):
                continue
            try:
                text = key.decode("utf-8")
            except UnicodeDecodeError:
                continue
            parts = text.split(":")
            if len(parts) < 4:
                continue
            loam_coord = parts[1]
            loam_block = int(parts[2])
            if loam_block > clay_block_height:
                continue
            loam_segments = self._coord_segments(loam_coord)
            if len(loam_segments) > len(clay_segments):
                continue
            if clay_segments[: len(loam_segments)] != loam_segments:
                continue
            # Do not include the Clay coord itself if it happens to be Loam.
            if loam_coord == normalized_clay:
                continue

            entry = self._store._decode_value(self._store._db[key])
            blocks_elapsed = max(clay_block_height - loam_block, 0)
            delta = math.log2(blocks_elapsed) if blocks_elapsed > 0 else 0.0
            decayed = {
                "v_awareness": max(entry["v_awareness"] - delta, 0.0),
                "v_unity": max(entry["v_unity"] - delta, 0.0),
                "v_ethics": max(entry["v_ethics"] - delta, 0.0),
            }
            results.append(
                {
                    "layer": constants.LAYER_LOAM,
                    "block_height": loam_block,
                    "coord": loam_coord,
                    "entry": entry,
                    "decayed_values": decayed,
                }
            )

        results.sort(key=lambda row: row["block_height"])
        return results
