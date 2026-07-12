"""Index-key builder for ingested semantic atoms.

Index keys contain only the geological layer, COORD, prime, and exponent.
Raw document text never appears in a key; only a content hash is stored in
the value payload.
"""

from __future__ import annotations

import hashlib
import json
from typing import Mapping


def build_index_entries(
    coord: str,
    exponents: Mapping[int, int],
    layer: str,
    raw_text: str,
) -> list[tuple[str, str]]:
    """Return RocksDB-style (key, value) tuples for ``coord`` and ``exponents``.

    Keys are ``{layer}:{coord}:{prime}:{v}``. Values are JSON containing the
    COORD, prime exponent map, layer, and a SHA-256 hash of the raw text.
    """
    content_hash = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()
    value_payload = json.dumps(
        {
            "coord": coord,
            "prime_exponents": {str(k): v for k, v in exponents.items() if v > 0},
            "layer": layer,
            "content_hash": content_hash,
        },
        sort_keys=True,
    )

    entries: list[tuple[str, str]] = []
    for prime, v in exponents.items():
        if v <= 0:
            continue
        key = f"{layer}:{coord}:{prime}:{v}"
        entries.append((key, value_payload))
    return entries


def index_key_contains_raw_text(key: str, text: str) -> bool:
    """Return ``True`` if ``text`` appears literally in ``key``."""
    return bool(text) and text in key
