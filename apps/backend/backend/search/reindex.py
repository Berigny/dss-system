"""Utilities for rebuilding the token index from existing ledger entries."""

from __future__ import annotations

import logging
import math
from typing import Any, Iterable, List, Tuple

from fastapi import FastAPI

from backend.fieldx_kernel.informational_unit import attach_core_informational_unit
from backend.fieldx_kernel.models import LedgerEntry
from backend.fieldx_kernel.p_adic import PrimeLatticeState
from backend.fieldx_kernel.qp_coordinate import derive_p_adic_coordinate
from backend.fieldx_kernel.substrate.ledger_store_v2 import (
    LedgerStoreV2,
    _collect_text_fragments,
)
from backend.search.token_index import TokenPrimeIndex, normalise_tokens


logger = logging.getLogger(__name__)


def _decode_key(raw_key: object) -> str:
    if isinstance(raw_key, (bytes, bytearray)):
        return raw_key.decode()
    return str(raw_key)


def _is_index_key(key: str) -> bool:
    return key.startswith("tp:") or key.startswith("ix:")


def _full_text_from_metadata(metadata: dict | None) -> str:
    if not metadata:
        return ""

    filtered = {
        key: value
        for key, value in metadata.items()
        if key not in {"full_text", "token_primes", "token_prime_product", "prime_lattice_exponents"}
    }
    fragments: Iterable[str] = _collect_text_fragments(filtered)
    return " ".join(str(fragment) for fragment in fragments)


def reindex_all(app: FastAPI, *, entity: str | None = None) -> dict:
    """
    Walk existing ledger entries and rebuild token primes + inverted index.

    Returns a summary dictionary with counts describing the work performed.
    """

    db = getattr(app.state, "db", None)
    if db is None:
        raise RuntimeError("Database not initialized on application state")

    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(db, token_index=token_index)

    ledger_rows: List[Tuple[str, LedgerEntry]] = []
    index_keys: List[object] = []

    # Internal key prefixes that are not ledger entries.
    skip_prefixes = (
        "body:",
        "overlay-history:",
        "overlay-seq:",
        "feedback:",
        "bucket:",
        "chain:",
        "attachment:",
        "metrics:",
        "tp:",
        "ix:",
        "entity:",
    )

    # Snapshot existing rows so we can safely mutate the DB afterwards.
    with store._lock:  # type: ignore[attr-defined]
        snapshot_items = list(db.items())

    for raw_key, raw_value in snapshot_items:
        decoded_key = _decode_key(raw_key)
        if _is_index_key(decoded_key):
            index_keys.append(raw_key)
            continue
        if decoded_key.startswith(skip_prefixes):
            continue

        if decoded_key.startswith(store.OVERLAY_PREFIX):
            ledger_id = decoded_key[len(store.OVERLAY_PREFIX) :]
            try:
                entry = store.read(ledger_id)
            except Exception:
                entry = None
            if entry is not None:
                ledger_rows.append((ledger_id, entry))
            continue

        # Backward compatibility: legacy combined entry records.
        if isinstance(raw_value, str):
            raw_bytes = raw_value.encode()
        elif isinstance(raw_value, (bytes, bytearray)):
            raw_bytes = raw_value
        else:
            continue
        try:
            entry = store._decode(raw_bytes)  # type: ignore[attr-defined]
            ledger_rows.append((decoded_key, entry))
        except Exception:
            logger.exception("Skipping malformed ledger row", extra={"entry_id": decoded_key})

    if index_keys:
        with store._lock:  # type: ignore[attr-defined]
            for idx_key in index_keys:
                try:
                    del db[idx_key]
                except KeyError:
                    continue

    logger.info(
        "Starting reindex",
        extra={
            "entity": entity,
            "ledger_rows": len(ledger_rows),
            "index_keys_cleared": len(index_keys),
        },
    )

    tokens_seen: set[str] = set()
    postings_written = 0
    keyword_postings_written = 0
    entries_reindexed = 0

    for entry_id, entry in ledger_rows:
        metadata = dict(entry.state.metadata)
        # Clear chain hashes so LedgerStoreV2 can recompute after reindex changes.
        metadata.pop("ledger_hash", None)
        metadata.pop("ledger_prev_hash", None)
        full_text = _full_text_from_metadata(metadata)
        tokens = normalise_tokens(full_text)
        primes = token_index.primes_for_tokens(tokens) if tokens else []

        metadata["full_text"] = full_text
        metadata["token_primes"] = primes
        lattice = PrimeLatticeState.from_primes(primes)
        metadata["prime_lattice_exponents"] = dict(lattice.exponents)
        # token_prime_product is no longer stored; it can be computed on demand.
        metadata.pop("token_prime_product", None)
        entry.state.metadata = metadata

        # Preserve any existing canonical p-adic coordinate so reindex stays idempotent.
        existing_p_adic_coordinate = metadata.get("p_adic_coordinate")

        # Re-derive the core informational unit (kernel exponents, factors, and the
        # canonical p-adic coordinate) from the updated token primes.
        try:
            attach_core_informational_unit(entry)
        except Exception:
            logger.exception("Failed to attach core informational unit", extra={"entry_id": entry_id})

        metadata = dict(entry.state.metadata)

        # Backfill canonical p-adic coordinate if missing, otherwise restore the
        # existing coordinate to keep reindex idempotent.
        if existing_p_adic_coordinate is not None:
            metadata["p_adic_coordinate"] = existing_p_adic_coordinate
        elif "p_adic_coordinate" not in metadata:
            coord = derive_p_adic_coordinate(metadata)
            if coord is not None:
                metadata["p_adic_coordinate"] = coord.as_dict()

        # Persist only the mutable overlay; the immutable body is untouched.
        store.update_metadata_overlay(entry.key.as_path(), metadata)  # type: ignore[attr-defined]

        if primes:
            token_index.update_inverted_index(primes, entry.key.as_path())
            postings_written += len(primes)

        keyword_sources: dict[str, float] = {
            "topics": 0.85,
            "tags": 0.7,
            "claims": 0.8,
            "summary_topics": 0.75,
            "attachment_summary": 0.6,
            "summary": 0.6,
        }

        def _keyword_tokens(value: Any) -> list[str]:
            tokens_list: list[str] = []
            if not value:
                return tokens_list
            if isinstance(value, str):
                tokens_list.extend(normalise_tokens(value))
            elif isinstance(value, (list, tuple, set)):
                for item in value:
                    if isinstance(item, str):
                        tokens_list.extend(normalise_tokens(item))
            return tokens_list

        keyword_prime_weights: dict[int, float] = {}
        for source, weight in keyword_sources.items():
            tokens_list = _keyword_tokens(metadata.get(source))[:50]
            if not tokens_list:
                continue
            keyword_primes = token_index.primes_for_tokens(tokens_list)
            for prime in keyword_primes:
                current = keyword_prime_weights.get(prime)
                if current is None or weight > current:
                    keyword_prime_weights[prime] = weight

        if keyword_prime_weights:
            token_index.update_keyword_index(keyword_prime_weights, entry.key.as_path())
            keyword_postings_written += len(keyword_prime_weights)
        tokens_seen.update(tokens)
        entries_reindexed += 1

    summary = {
        "entity": entity,
        "entries_reindexed": entries_reindexed,
        "tokens_indexed": len(tokens_seen),
        "postings_updated": postings_written,
        "keyword_postings_updated": keyword_postings_written,
        "cleared_index_keys": len(index_keys),
    }

    logger.info("Reindex complete", extra=summary)
    return summary


__all__ = ["reindex_all"]
