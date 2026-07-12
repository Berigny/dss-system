"""Persistent ledger storage backed by RocksDB."""

from __future__ import annotations

import json
import math
import os
import hashlib
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Iterable, Mapping, MutableMapping, Optional

from backend.fieldx_kernel.kernel_origin_equations import calculate_persistence_cost
from backend.fieldx_kernel.informational_unit import attach_core_informational_unit
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.p_adic import PAdicInteger, PrimeLatticeState
from backend.fieldx_kernel.schema import FLOW_PRIMES, MIN_BODY_PRIME
from backend.fieldx_kernel.substrate.padic_ledger_store import PAdicLedgerStore
from backend.search.token_index import TokenPrimeIndex, normalise_tokens
from shared_types.coord_schema import normalize_coordinate_metadata, sanitize_coordinate_metadata

LEDGER_READ_VERIFY_STRICT = os.getenv("LEDGER_READ_VERIFY_STRICT", "0") == "1"


def _collect_text_fragments(
    value: Any,
    visited: set[int] | None = None,
    _seen_strings: set[int] | None = None,
) -> Iterable[str]:
    if visited is None:
        visited = set()
    if _seen_strings is None:
        _seen_strings = set()

    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return
        content_hash = hash(trimmed)
        if content_hash in _seen_strings:
            return
        _seen_strings.add(content_hash)
        yield trimmed
        return

    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in visited:
            return
        visited.add(container_id)
        structural_keys = {
            "web4_key",
            "provider",
            "session_id",
            "timestamp",
            "full_text",
            "attachment_parts",
        }
        for key, item in value.items():
            if isinstance(key, str) and key in structural_keys:
                continue
            yield from _collect_text_fragments(item, visited, _seen_strings)
        return

    if isinstance(value, (list, tuple, set)):
        container_id = id(value)
        if container_id in visited:
            return
        visited.add(container_id)
        for item in value:
            yield from _collect_text_fragments(item, visited, _seen_strings)


def _token_product_residue(primes: Sequence[int], p: int, N: int) -> int:
    """Return ``prod(primes) mod p**N`` without materialising the full product."""
    if not primes:
        return 0
    modulus = p**N
    residue = 1
    for prime in primes:
        residue = (residue * (int(prime) % modulus)) % modulus
    return residue


def _full_text_for_entry(entry: LedgerEntry) -> str:
    text = getattr(entry, "text", None)
    if text:
        return str(text)

    body = getattr(entry, "body", None)
    if body is not None:
        return str(body)

    metadata = entry.state.metadata or {}
    fragments = list(_collect_text_fragments(metadata))
    if fragments:
        return " ".join(str(fragment) for fragment in fragments)

    return ""


class LedgerStoreV2:
    """Ledger storage that persists entries in a RocksDB dictionary."""

    PINNED_BUCKET = 23
    ATTACHMENT_HASH_PREFIX = "attachment:hash:"
    LEDGER_CHAIN_PREFIX = "chain:last:"
    FEEDBACK_STATE_PREFIX = "feedback:state:"
    BODY_PREFIX = "body:"
    OVERLAY_PREFIX = "overlay:"
    OVERLAY_HISTORY_PREFIX = "overlay-history:"
    OVERLAY_SEQ_PREFIX = "overlay-seq:"
    BLOB_PREFIX = "blob:"

    def __init__(
        self,
        db: MutableMapping[bytes, bytes],
        token_index: TokenPrimeIndex | None = None,
        padic_store: PAdicLedgerStore | None = None,
    ):
        self._db = db
        self._lock = RLock()
        self._token_index = token_index
        self._padic_store = padic_store
        if self._padic_store is None:
            try:
                p = int(os.getenv("PADIC_LEDGER_PRIME", "5"))
                N = int(os.getenv("PADIC_LEDGER_PRECISION", "4"))
                self._padic_store = PAdicLedgerStore(self._db, p, N)
            except Exception:
                self._padic_store = None

    def _pinned_key(self, entry_id: str) -> bytes:
        return f"bucket:{self.PINNED_BUCKET}:{entry_id}".encode()

    def _pinned_index_key(self) -> bytes:
        return f"bucket:{self.PINNED_BUCKET}:index".encode()

    def _attachment_hash_key(self, sha256: str) -> bytes:
        return f"{self.ATTACHMENT_HASH_PREFIX}{sha256}".encode()

    def _ledger_chain_key(self, namespace: str) -> bytes:
        return f"{self.LEDGER_CHAIN_PREFIX}{namespace}".encode()

    def _feedback_state_key(self, ledger_id: str) -> bytes:
        return f"{self.FEEDBACK_STATE_PREFIX}{ledger_id}".encode()

    def _body_key(self, body_hash: str) -> bytes:
        return f"{self.BODY_PREFIX}{body_hash}".encode()

    def _blob_storage_key(self, coordinate: str) -> bytes:
        return f"{self.BLOB_PREFIX}{coordinate}".encode()

    def _overlay_key(self, ledger_id: str) -> bytes:
        return f"{self.OVERLAY_PREFIX}{ledger_id}".encode()

    def _overlay_history_key(self, ledger_id: str, seq: int) -> bytes:
        return f"{self.OVERLAY_HISTORY_PREFIX}{ledger_id}:{seq}".encode()

    def _overlay_seq_key(self, ledger_id: str) -> bytes:
        return f"{self.OVERLAY_SEQ_PREFIX}{ledger_id}".encode()

    @staticmethod
    def _body_hash(body_bytes: bytes) -> str:
        return hashlib.sha256(body_bytes).hexdigest()

    @staticmethod
    def _normalize_created_at(created_at: datetime) -> datetime:
        """Return ``created_at`` as a timezone-aware UTC datetime."""
        if created_at.tzinfo is None:
            return created_at.replace(tzinfo=timezone.utc)
        return created_at.astimezone(timezone.utc)

    def _canonical_body_bytes(self, entry: LedgerEntry) -> bytes:
        """Return the immutable body bytes for an entry.

        The body contains only stable, content-addressed fields: coordinates,
        phase, notes, and creation time.  Identity (key/ledger_id) and mutable
        metadata (pinned, feedback, hashes) live in the overlay, so identical
        body content deduplicates to a single stored body record.
        """
        payload = {
            "coordinates": dict(entry.state.coordinates),
            "phase": entry.state.phase,
            "notes": entry.notes,
            "created_at": self._normalize_created_at(entry.created_at).isoformat(),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def _encode_overlay(self, entry: LedgerEntry, body_hash: str) -> bytes:
        """Return the mutable overlay bytes for an entry."""
        metadata = sanitize_coordinate_metadata(dict(entry.state.metadata))
        payload = {
            "key": {"namespace": entry.key.namespace, "identifier": entry.key.identifier},
            "body_hash": body_hash,
            "metadata": metadata,
            "pinned": entry.pinned,
            "created_at": entry.created_at.isoformat(),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def _read_overlay(self, ledger_id: str) -> dict[str, Any] | None:
        """Load and parse the overlay record for ``ledger_id``."""
        raw = self._db.get(self._overlay_key(ledger_id))
        if raw is None:
            return None
        try:
            decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            payload = json.loads(decoded)
            return payload if isinstance(payload, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def _read_body(self, body_hash: str) -> dict[str, Any] | None:
        """Load and parse the immutable body for ``body_hash``."""
        raw = self._db.get(self._body_key(body_hash))
        if raw is None:
            return None
        try:
            decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            payload = json.loads(decoded)
            return payload if isinstance(payload, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def _entry_from_split(self, ledger_id: str, overlay: Mapping[str, Any]) -> LedgerEntry | None:
        """Reconstruct a ``LedgerEntry`` from its overlay and body."""
        body_hash = overlay.get("body_hash")
        if not isinstance(body_hash, str):
            return None
        body = self._read_body(body_hash)
        if body is None:
            return None

        key_data = overlay.get("key")
        if not isinstance(key_data, dict):
            return None

        created_at_str = body.get("created_at") or overlay.get("created_at")
        created_at = datetime.fromisoformat(created_at_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        pinned = bool(overlay.get("pinned", False))
        metadata = normalize_coordinate_metadata(dict(overlay.get("metadata", {})))

        return LedgerEntry(
            key=LedgerKey(namespace=key_data["namespace"], identifier=key_data["identifier"]),
            state=ContinuousState(
                coordinates=dict(body.get("coordinates", {})),
                phase=body.get("phase"),
                metadata=metadata,
            ),
            created_at=created_at,
            notes=body.get("notes"),
            pinned=pinned,
        )

    def _load_feedback_state(self, ledger_id: str) -> dict[str, Any]:
        with self._lock:
            raw = self._db.get(self._feedback_state_key(ledger_id))
        if raw is None:
            return {}
        try:
            decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            payload = json.loads(decoded)
            return normalize_coordinate_metadata(payload) if isinstance(payload, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return {}

    def _store_feedback_state(self, ledger_id: str, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(sanitize_coordinate_metadata(dict(payload))).encode()
        with self._lock:
            self._db[self._feedback_state_key(ledger_id)] = encoded

    def _load_pinned_ids(self) -> set[str]:
        raw = self._db.get(self._pinned_index_key())
        if raw is None:
            return set()
        try:
            decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            items = json.loads(decoded)
            return {str(item) for item in items}
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return set()

    def get_attachment_coordinate(self, sha256: str) -> str | None:
        with self._lock:
            raw = self._db.get(self._attachment_hash_key(sha256))
        if raw is None:
            return None
        try:
            return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
        except Exception:
            return None

    def set_attachment_coordinate(self, sha256: str, coordinate: str) -> None:
        if not sha256 or not coordinate:
            return
        with self._lock:
            self._db[self._attachment_hash_key(sha256)] = coordinate.encode()

    def write_blob(
        self,
        entity: str,
        raw_text: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist the intact ``raw_text`` as a content-addressed blob.

        The blob is stored outside the normal ledger body/overlay split so that
        the full payload can be retrieved independently of any kernel projection.
        Identical payloads deduplicate to the same coordinate.
        """
        raw_bytes = (raw_text or "").encode("utf-8")
        blob_hash = hashlib.sha256(raw_bytes).hexdigest()
        coordinate = f"{entity}:blob-{blob_hash}"
        key = self._blob_storage_key(coordinate)

        with self._lock:
            existing = self._db.get(key)
            if existing is not None:
                try:
                    payload = json.loads(existing)
                    if isinstance(payload, dict):
                        return {
                            "coordinate": coordinate,
                            "blob_hash": blob_hash,
                            "byte_length": payload.get("byte_length", len(raw_bytes)),
                            "created_at": payload.get("created_at"),
                            "deduplicated": True,
                        }
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            payload = {
                "coordinate": coordinate,
                "entity": entity,
                "blob_hash": blob_hash,
                "byte_length": len(raw_bytes),
                "raw_text": raw_text or "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "metadata": dict(metadata) if metadata else {},
            }
            self._db[key] = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

        return {
            "coordinate": coordinate,
            "blob_hash": blob_hash,
            "byte_length": len(raw_bytes),
            "created_at": payload["created_at"],
            "deduplicated": False,
        }

    def read_blob(self, coordinate: str) -> dict[str, Any] | None:
        """Load a previously stored blob by its coordinate."""
        key = self._blob_storage_key(coordinate)
        with self._lock:
            raw = self._db.get(key)
        if raw is None:
            return None
        try:
            decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            payload = json.loads(decoded)
            return payload if isinstance(payload, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def read_blob_text(self, coordinate: str) -> str | None:
        """Return the raw text of a blob, or ``None`` if missing."""
        payload = self.read_blob(coordinate)
        if payload is None:
            return None
        raw = payload.get("raw_text")
        return str(raw) if raw is not None else None

    def has_blob(self, coordinate: str) -> bool:
        """Return ``True`` iff a blob exists at ``coordinate``."""
        return self._db.get(self._blob_storage_key(coordinate)) is not None

    def _sync_pinned_metadata(self, entry: LedgerEntry) -> None:
        metadata = dict(entry.state.metadata)
        metadata["pinned"] = entry.pinned
        entry.state.metadata = metadata

    def _update_pin_index(self, entry_id: str, pinned: bool) -> None:
        index_key = self._pinned_index_key()
        with self._lock:
            pinned_ids = self._load_pinned_ids()

            if pinned:
                pinned_ids.add(entry_id)
                self._db[self._pinned_key(entry_id)] = b"1"
            else:
                pinned_ids.discard(entry_id)
                try:
                    del self._db[self._pinned_key(entry_id)]
                except KeyError:
                    pass

            if pinned_ids:
                self._db[index_key] = json.dumps(sorted(pinned_ids)).encode()
            else:
                try:
                    del self._db[index_key]
                except KeyError:
                    pass

    def _apply_pin_status(self, entry_id: str, entry: LedgerEntry) -> None:
        if entry.pinned:
            self._update_pin_index(entry_id, True)
        else:
            self._update_pin_index(entry_id, False)

    def _encode(self, entry: LedgerEntry) -> bytes:
        self._sync_pinned_metadata(entry)
        payload = {
            "key": {"namespace": entry.key.namespace, "identifier": entry.key.identifier},
            "state": {
                "coordinates": dict(entry.state.coordinates),
                "phase": entry.state.phase,
                "metadata": dict(entry.state.metadata),
            },
            "created_at": entry.created_at.isoformat(),
            "notes": entry.notes,
            "pinned": entry.pinned,
        }
        return json.dumps(payload).encode()

    def _entry_fingerprint(self, entry: LedgerEntry) -> str:
        metadata = dict(entry.state.metadata or {})
        metadata.pop("ledger_hash", None)
        metadata.pop("ledger_prev_hash", None)
        metadata.pop("pinned", None)
        # Derived energy/index fields are not part of the deterministic entry
        # fingerprint; they are recomputed at write time.
        metadata.pop("p_adic_write_cost", None)
        created_at = entry.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        payload = {
            "key": {"namespace": entry.key.namespace, "identifier": entry.key.identifier},
            "state": {
                "coordinates": dict(entry.state.coordinates),
                "phase": entry.state.phase,
                "metadata": metadata,
            },
            "created_at": created_at.isoformat(),
            "notes": entry.notes,
            "pinned": entry.pinned,
        }
        encoded = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]

    def _replay_chain_valid(self, namespace: str, *, expected_prev_hash: str) -> bool:
        entries = self.list_by_namespace(namespace, limit=None, reverse=False)
        prev_hash = "genesis"
        for entry in entries:
            fp = self._entry_fingerprint(entry)
            computed = hashlib.sha256(f"{prev_hash}:{fp}".encode()).hexdigest()[:16]
            meta_hash = entry.state.metadata.get("ledger_hash")
            if meta_hash and str(meta_hash) != computed:
                return False
            prev_hash = computed
        return prev_hash == expected_prev_hash

    def verify_namespace_chain(self, namespace: str) -> dict[str, Any]:
        entries = self.list_by_namespace(namespace, limit=None, reverse=False)
        prev_hash = "genesis"
        checked = 0
        failure_reason: str | None = None
        failed_entry_id: str | None = None

        for entry in entries:
            checked += 1
            fp = self._entry_fingerprint(entry)
            computed = hashlib.sha256(f"{prev_hash}:{fp}".encode()).hexdigest()[:16]
            meta_hash = entry.state.metadata.get("ledger_hash")
            if not meta_hash:
                failure_reason = "missing_entry_hash"
                failed_entry_id = entry.key.as_path()
                break
            if str(meta_hash) != computed:
                failure_reason = "entry_hash_mismatch"
                failed_entry_id = entry.key.as_path()
                break
            prev_hash = computed

        with self._lock:
            expected_raw = self._db.get(self._ledger_chain_key(namespace))
        expected_prev_hash = (
            expected_raw.decode() if isinstance(expected_raw, (bytes, bytearray)) else expected_raw
        )
        expected_prev_hash = str(expected_prev_hash) if expected_prev_hash else "genesis"

        if failure_reason is None and prev_hash != expected_prev_hash:
            failure_reason = "chain_tip_mismatch"

        return {
            "namespace": namespace,
            "valid": failure_reason is None,
            "entries_checked": checked,
            "computed_tip_hash": prev_hash,
            "stored_tip_hash": expected_prev_hash,
            "failure_reason": failure_reason,
            "failed_entry_id": failed_entry_id,
        }

    def _decode(self, payload: bytes) -> LedgerEntry:
        data = json.loads(payload)
        key_data = data["key"]
        state_data = data["state"]

        created_at_str = data["created_at"]
        created_at = datetime.fromisoformat(created_at_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        pinned = bool(data.get("pinned", state_data.get("metadata", {}).get("pinned", False)))

        entry = LedgerEntry(
            key=LedgerKey(namespace=key_data["namespace"], identifier=key_data["identifier"]),
            state=ContinuousState(
                coordinates=dict(state_data.get("coordinates", {})),
                phase=state_data.get("phase"),
                metadata=dict(state_data.get("metadata", {})),
            ),
            created_at=created_at,
            notes=data.get("notes"),
            pinned=pinned,
        )

        self._sync_pinned_metadata(entry)
        return entry

    def write(self, entry: LedgerEntry) -> None:
        """Persist the ledger entry using its path as the key."""

        entry_id = entry.key.as_path()

        # Load the previous lattice (if any) so the inverted index can be
        # updated sparsely by delta instead of rewritten for every prime.
        old_entry = self.read(entry_id)
        old_lattice: PrimeLatticeState | None = None
        if old_entry is not None:
            old_exponents = old_entry.state.metadata.get("prime_lattice_exponents")
            if isinstance(old_exponents, dict):
                old_lattice = PrimeLatticeState(old_exponents)
            else:
                old_primes = old_entry.state.metadata.get("token_primes", [])
                if old_primes:
                    old_lattice = PrimeLatticeState.from_primes(old_primes)

        full_text = _full_text_for_entry(entry)
        primes = self._index_entry(entry, full_text)

        # Attach the core informational unit aligned to the 0-9 Computational
        # Lattice. This makes every written entry a factor-bearing candidate for
        # p-adic retrieval and enrichment.
        attach_core_informational_unit(entry)

        new_lattice = PrimeLatticeState.from_primes(primes)
        if old_lattice is None:
            index_deltas = {prime: 1 for prime in primes}
        else:
            index_deltas = new_lattice.delta(old_lattice)

        metadata = dict(entry.state.metadata)
        chain_key = self._ledger_chain_key(entry.key.namespace)
        with self._lock:
            prev_hash_raw = self._db.get(chain_key)
        prev_hash = (
            prev_hash_raw.decode() if isinstance(prev_hash_raw, (bytes, bytearray)) else prev_hash_raw
        )
        prev_hash = str(prev_hash) if prev_hash else "genesis"
        entry_fingerprint = self._entry_fingerprint(entry)
        expected_hash = hashlib.sha256(f"{prev_hash}:{entry_fingerprint}".encode()).hexdigest()[:16]
        provided_hash = metadata.get("ledger_hash")
        if provided_hash and str(provided_hash) != expected_hash:
            raise ValueError("Deterministic replay check failed: ledger_hash mismatch")
        if os.getenv("GOVERNANCE_REPLAY", "0") == "1":
            if not self._replay_chain_valid(entry.key.namespace, expected_prev_hash=prev_hash):
                raise ValueError("Deterministic replay check failed: chain mismatch")
        metadata["ledger_prev_hash"] = prev_hash
        metadata["ledger_hash"] = expected_hash

        # Discrete p-adic write-cost term for the energy model.
        lambda_p = float(os.getenv("PADIC_WRITE_COST_LAMBDA", "0.0") or 0.0)
        if lambda_p and index_deltas:
            metadata["p_adic_write_cost"] = calculate_persistence_cost(
                0.0, 1.0, 0, lattice_delta=index_deltas, lambda_p=lambda_p
            )
        else:
            metadata["p_adic_write_cost"] = 0.0

        entry.state.metadata = metadata

        # Write-once body layer: immutable, content-addressed body bytes.
        body_bytes = self._canonical_body_bytes(entry)
        body_hash = self._body_hash(body_bytes)
        body_key = self._body_key(body_hash)
        overlay_key = self._overlay_key(entry_id)
        overlay_bytes = self._encode_overlay(entry, body_hash)

        with self._lock:
            existing_body = self._db.get(body_key)
            if existing_body is not None and existing_body != body_bytes:
                raise ValueError(
                    f"body hash collision for {entry_id}: cannot overwrite existing body"
                )
            self._db[body_key] = body_bytes
            self._db[overlay_key] = overlay_bytes
            self._apply_pin_status(entry_id, entry)
            if index_deltas and self._token_index:
                self._token_index.update_inverted_index_delta(index_deltas, entry_id)
            self._db[chain_key] = expected_hash.encode()
            self._write_padic_ball(entry)
            self._write_token_padic_ball(entry, primes)

    def _write_padic_ball(self, entry: LedgerEntry) -> None:
        """Write the entry path into the p-adic ball store for integer identifiers."""
        if self._padic_store is None:
            return

        identifier = entry.key.identifier
        if not identifier.isdigit():
            return

        try:
            state = PAdicInteger.from_int(
                self._padic_store.p,
                int(identifier),
                self._padic_store.N,
            )
            self._padic_store.write(
                entry.key.namespace,
                state,
                entry.key.as_path().encode(),
            )
        except Exception:
            # p-adic ball storage is a best-effort optimisation; do not fail
            # the main ledger write.
            return

    def _write_token_padic_ball(self, entry: LedgerEntry, primes: list[int]) -> None:
        """Write a token-product residue ball so orchestrator retrieval can pre-filter."""
        if self._padic_store is None or not primes:
            return

        try:
            residue = _token_product_residue(
                primes, self._padic_store.p, self._padic_store.N
            )
            state = PAdicInteger.from_int(
                self._padic_store.p, residue, self._padic_store.N
            )
            namespace = f"tp:{entry.key.namespace}"
            self._padic_store.write(
                namespace, state, entry.key.identifier.encode()
            )
        except Exception:
            return

    def read(self, ledger_id: str, *, verify_chain: bool | None = None) -> Optional[LedgerEntry]:
        """Retrieve a ledger entry by its encoded identifier path.

        Reconstructs the entry from the immutable body and the latest overlay.
        Falls back to the legacy combined encoding if no overlay record exists.
        """
        with self._lock:
            overlay = self._read_overlay(ledger_id)
        if overlay is not None:
            entry = self._entry_from_split(ledger_id, overlay)
            if entry is None:
                return None
        else:
            # Backward compatibility: legacy combined entry records.
            with self._lock:
                encoded = self._db.get(ledger_id.encode())
            if encoded is None:
                return None
            entry = self._decode(encoded)

        strict = LEDGER_READ_VERIFY_STRICT if verify_chain is None else bool(verify_chain)
        if strict:
            status = self.verify_namespace_chain(entry.key.namespace)
            if not status.get("valid"):
                reason = status.get("failure_reason") or "chain_verification_failed"
                raise ValueError(f"Read-time chain verification failed: {reason}")
        return entry

    # Compatibility helpers for existing callers expecting the v1 API
    def upsert(self, entry: LedgerEntry) -> None:  # pragma: no cover - thin wrapper
        self.write(entry)

    def get(self, key: LedgerKey) -> Optional[LedgerEntry]:  # pragma: no cover - thin wrapper
        return self.read(key.as_path())

    def _next_overlay_seq(self, ledger_id: str) -> int:
        """Return the next monotonic sequence number for overlay history."""
        seq_key = self._overlay_seq_key(ledger_id)
        raw = self._db.get(seq_key)
        seq = 0
        if raw is not None:
            try:
                seq = int(raw.decode()) + 1
            except (UnicodeDecodeError, ValueError, TypeError):
                seq = 0
        self._db[seq_key] = str(seq).encode()
        return seq

    def _write_overlay(self, ledger_id: str, entry: LedgerEntry) -> None:
        """Persist the mutable overlay for ``ledger_id`` from ``entry``.

        Writes both the latest overlay pointer and an append-only history
        snapshot so every mutation is recorded without rewriting the body.
        """
        body_bytes = self._canonical_body_bytes(entry)
        body_hash = self._body_hash(body_bytes)
        overlay_bytes = self._encode_overlay(entry, body_hash)
        with self._lock:
            seq = self._next_overlay_seq(ledger_id)
            self._db[self._overlay_history_key(ledger_id, seq)] = overlay_bytes
            self._db[self._overlay_key(ledger_id)] = overlay_bytes

    def update_metadata_overlay(
        self,
        ledger_id: str,
        metadata_updates: Mapping[str, Any],
        *,
        replace: bool = False,
    ) -> LedgerEntry | None:
        """Merge ``metadata_updates`` into the latest overlay without touching the body.

        Chain-hash fields are stripped from the result because they were
        computed for the original body/overlay snapshot and are not recomputed
        here.  Returns the updated entry, or ``None`` if ``ledger_id`` is unknown.
        """
        entry = self.read(ledger_id)
        if entry is None:
            return None

        if replace:
            metadata: dict[str, Any] = dict(metadata_updates)
        else:
            metadata = dict(entry.state.metadata or {})
            metadata.update(metadata_updates)

        metadata.pop("ledger_hash", None)
        metadata.pop("ledger_prev_hash", None)
        entry.state.metadata = metadata

        self._write_overlay(ledger_id, entry)
        return entry

    def set_pinned(self, ledger_id: str, pinned: bool) -> Optional[LedgerEntry]:
        entry = self.read(ledger_id)
        if entry is None:
            return None

        entry.pinned = bool(pinned)
        metadata = dict(entry.state.metadata or {})
        metadata["pinned"] = entry.pinned
        entry.state.metadata = metadata
        self._write_overlay(ledger_id, entry)

        state = self._load_feedback_state(ledger_id)
        state["pinned"] = bool(pinned)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._store_feedback_state(ledger_id, state)
        self._update_pin_index(ledger_id, bool(pinned))
        return entry

    def _feedback_rollup_from_metadata(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        feedback = metadata.get("feedback")
        if not isinstance(feedback, Mapping):
            return {"score": None, "actors": 0, "samples": 0, "by_actor": {}, "updated_at": None}

        by_actor_raw = feedback.get("by_actor")
        by_actor = by_actor_raw if isinstance(by_actor_raw, Mapping) else {}
        actor_totals: list[float] = []
        actor_summaries: dict[str, Any] = {}
        sample_days = 0
        latest_ts: str | None = None

        for actor_id, actor_payload in by_actor.items():
            if not isinstance(actor_id, str) or not isinstance(actor_payload, Mapping):
                continue
            day_map_raw = actor_payload.get("days")
            day_map = day_map_raw if isinstance(day_map_raw, Mapping) else {}
            day_scores: list[float] = []
            actor_last_ts: str | None = None
            for day_key, day_payload in day_map.items():
                _ = day_key
                if not isinstance(day_payload, Mapping):
                    continue
                rating = day_payload.get("rating")
                if not isinstance(rating, (int, float)):
                    continue
                rating_v = max(0.0, min(3.0, float(rating)))
                day_scores.append(rating_v)
                sample_days += 1
                ts = day_payload.get("ts")
                if isinstance(ts, str) and ts:
                    if actor_last_ts is None or ts > actor_last_ts:
                        actor_last_ts = ts
                    if latest_ts is None or ts > latest_ts:
                        latest_ts = ts
            if not day_scores:
                continue
            actor_total = sum(day_scores) / float(len(day_scores))
            actor_totals.append(actor_total)
            actor_summaries[actor_id] = {
                "actor_type": actor_payload.get("actor_type"),
                "score": round(actor_total, 4),
                "days": len(day_scores),
                "last_rated_at": actor_last_ts,
            }

        score = None
        if actor_totals:
            score = round(sum(actor_totals) / float(len(actor_totals)), 4)
        return {
            "score": score,
            "actors": len(actor_totals),
            "samples": sample_days,
            "by_actor": actor_summaries,
            "updated_at": latest_ts,
        }

    def submit_feedback(
        self,
        ledger_id: str,
        *,
        actor_id: str,
        actor_type: str,
        rating: int,
        reason: str | None = None,
        source: str | None = None,
        ts: str | None = None,
    ) -> Optional[LedgerEntry]:
        entry = self.read(ledger_id)
        if entry is None:
            return None

        safe_actor = (actor_id or "").strip() or "unknown"
        safe_type = (actor_type or "").strip() or "human"
        rating_v = max(0, min(3, int(rating)))
        now = ts or datetime.now(timezone.utc).isoformat()
        day_key = now[:10]

        state = self._load_feedback_state(ledger_id)
        feedback = state.get("feedback")
        if not isinstance(feedback, dict):
            feedback = {}

        by_actor = feedback.get("by_actor")
        if not isinstance(by_actor, dict):
            by_actor = {}
        actor_payload = by_actor.get(safe_actor)
        if not isinstance(actor_payload, dict):
            actor_payload = {"actor_type": safe_type, "days": {}}
        actor_payload["actor_type"] = safe_type
        days = actor_payload.get("days")
        if not isinstance(days, dict):
            days = {}
        # Anti-gaming: one effective value per actor per day; latest write replaces the day value.
        day_payload = days.get(day_key)
        if not isinstance(day_payload, dict):
            day_payload = {"count": 0}
        day_payload["rating"] = rating_v
        day_payload["reason"] = (reason or "").strip()
        day_payload["ts"] = now
        day_payload["source"] = (source or "").strip()
        day_payload["count"] = int(day_payload.get("count") or 0) + 1
        days[day_key] = day_payload
        actor_payload["days"] = days
        by_actor[safe_actor] = actor_payload
        feedback["by_actor"] = by_actor

        events = feedback.get("events")
        if not isinstance(events, list):
            events = []
        events.append(
            {
                "actor_id": safe_actor,
                "actor_type": safe_type,
                "rating": rating_v,
                "reason": (reason or "").strip(),
                "source": (source or "").strip(),
                "ts": now,
                "day": day_key,
            }
        )
        feedback["events"] = events[-64:]
        rollup = self._feedback_rollup_from_metadata({"feedback": feedback})

        # Compatibility with existing pin consumers: pin when rollup score >= 2.0.
        rollup_score = rollup.get("score")
        pinned = bool(isinstance(rollup_score, (int, float)) and float(rollup_score) >= 2.0)
        state["feedback"] = feedback
        state["feedback_rollup"] = rollup
        state["pinned"] = pinned
        state["updated_at"] = now
        self._store_feedback_state(ledger_id, state)
        self._update_pin_index(ledger_id, pinned)

        # Do not mutate the base ledger entry payload; return an enriched view only.
        metadata = dict(entry.state.metadata or {})
        metadata["feedback_rollup"] = rollup
        metadata["pinned"] = pinned
        entry.state.metadata = metadata
        entry.pinned = pinned
        self._write_overlay(ledger_id, entry)
        return entry

    def get_feedback(self, ledger_id: str) -> Optional[dict[str, Any]]:
        entry = self.read(ledger_id)
        if entry is None:
            return None
        sidecar = self._load_feedback_state(ledger_id)
        feedback_sidecar = sidecar.get("feedback")
        rollup_sidecar = sidecar.get("feedback_rollup")
        pinned_sidecar = sidecar.get("pinned")
        if isinstance(feedback_sidecar, dict):
            return {
                "entry_id": ledger_id,
                "rollup": rollup_sidecar if isinstance(rollup_sidecar, dict) else self._feedback_rollup_from_metadata({"feedback": feedback_sidecar}),
                "feedback": feedback_sidecar,
                "pinned": bool(pinned_sidecar),
            }
        metadata = dict(entry.state.metadata or {})
        feedback = metadata.get("feedback")
        if not isinstance(feedback, dict):
            feedback = {"by_actor": {}, "events": []}
        rollup = metadata.get("feedback_rollup")
        if not isinstance(rollup, dict):
            rollup = self._feedback_rollup_from_metadata(metadata)
        return {
            "entry_id": ledger_id,
            "rollup": rollup,
            "feedback": feedback,
            "pinned": bool(entry.pinned),
        }

    def is_pinned(self, ledger_id: str) -> bool:
        return self._db.get(self._pinned_key(ledger_id)) is not None

    def list_pinned_entries(self, namespace: str | None = None) -> list[LedgerEntry]:
        pinned_ids = self._load_pinned_ids()
        if namespace:
            prefix = f"{namespace}:"
            pinned_ids = {entry_id for entry_id in pinned_ids if entry_id.startswith(prefix)}

        entries: list[LedgerEntry] = []
        for entry_id in sorted(pinned_ids):
            entry = self.read(entry_id)
            if entry is None:
                self._update_pin_index(entry_id, False)
                continue
            entry.pinned = True
            metadata = dict(entry.state.metadata or {})
            metadata["pinned"] = True
            entry.state.metadata = metadata
            entries.append(entry)

        return entries

    def list_all_entries(self, limit: int) -> list[LedgerEntry]:
        """List the most recent ledger entries across all namespaces."""
        entries = []
        overlay_prefix = f"{self.OVERLAY_PREFIX}".encode()
        with self._lock:
            if not hasattr(self._db, "iterkeys"):
                for raw_key in list(self._db.keys()):
                    key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
                    ledger_id = self._ledger_id_from_overlay_key(key_bytes)
                    if ledger_id is None:
                        continue
                    entry = self.read(ledger_id)
                    if entry is not None:
                        entries.append(entry)
                    if len(entries) >= limit:
                        break
                entries.sort(key=lambda e: e.created_at, reverse=True)
                return entries[:limit] if limit else entries

            keys_iterator = self._db.iterkeys()  # type: ignore[attr-defined]
            keys_iterator.seek(overlay_prefix)
            for raw_key in keys_iterator:
                key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
                if not key_bytes.startswith(overlay_prefix):
                    break
                ledger_id = self._ledger_id_from_overlay_key(key_bytes)
                if ledger_id is None:
                    continue
                entry = self.read(ledger_id)
                if entry is not None:
                    entries.append(entry)
                if len(entries) >= limit:
                    break

        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries[:limit] if limit else entries

    def _ledger_id_from_overlay_key(self, key_bytes: bytes) -> str | None:
        """Extract ``namespace:identifier`` from an ``overlay:...`` key."""
        prefix = f"{self.OVERLAY_PREFIX}".encode()
        if not key_bytes.startswith(prefix):
            return None
        return key_bytes[len(prefix) :].decode()

    def list_by_namespace(
        self, namespace: str, limit: int | None = None, reverse: bool = True
    ) -> list[LedgerEntry]:
        """
        List ledger entries within a namespace using an efficient prefix scan.

        Entries are reconstructed from the immutable body and latest overlay.
        """
        overlay_prefix = f"{self.OVERLAY_PREFIX}{namespace}:".encode()
        entries = []

        with self._lock:
            if not hasattr(self._db, "iterkeys"):
                return self._list_by_namespace_fallback(namespace, limit, reverse)

            keys_iterator = self._db.iterkeys()  # type: ignore[attr-defined]
            keys_iterator.seek(overlay_prefix)

            for raw_key in keys_iterator:
                key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
                if not key_bytes.startswith(overlay_prefix):
                    break

                ledger_id = self._ledger_id_from_overlay_key(key_bytes)
                if ledger_id is None:
                    continue
                entry = self.read(ledger_id)
                if entry is not None:
                    entries.append(entry)

        entries.sort(key=lambda e: e.created_at, reverse=reverse)
        if limit:
            return entries[:limit]
        return entries

    def _list_by_namespace_fallback(
        self, namespace: str, limit: int | None = None, reverse: bool = True
    ) -> list[LedgerEntry]:
        """Inefficient fallback for listing entries by namespace via full scan."""
        entries = []
        ns_prefix = f"{namespace}:"
        with self._lock:
            for raw_key in self._db.keys():
                key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
                ledger_id = self._ledger_id_from_overlay_key(key_bytes)
                if ledger_id is None or not ledger_id.startswith(ns_prefix):
                    continue
                entry = self.read(ledger_id)
                if entry is not None:
                    entries.append(entry)

        entries.sort(key=lambda e: e.created_at, reverse=reverse)
        if limit:
            return entries[:limit]
        return entries

    def _index_entry(self, entry: LedgerEntry, full_text: str) -> list[int]:
        metadata = dict(entry.state.metadata)
        metadata["pinned"] = entry.pinned
        metadata["full_text"] = full_text

        if self._token_index is None:
            entry.state.metadata = metadata
            return []

        keyword_sources: dict[str, float] = {
            "topics": 0.85,
            "tags": 0.7,
            "claims": 0.8,
            "summary_topics": 0.75,
            "attachment_summary": 0.6,
            "summary": 0.6,
        }

        def _keyword_tokens(value: Any) -> list[str]:
            tokens: list[str] = []
            if not value:
                return tokens
            if isinstance(value, str):
                tokens.extend(normalise_tokens(value))
            elif isinstance(value, (list, tuple, set)):
                for item in value:
                    if isinstance(item, str):
                        tokens.extend(normalise_tokens(item))
            return tokens

        keyword_prime_weights: dict[int, float] = {}
        for source, weight in keyword_sources.items():
            tokens = _keyword_tokens(metadata.get(source))[:50]
            if not tokens:
                continue
            primes = [
                prime
                for prime in self._token_index.primes_for_tokens(tokens)
                if prime >= MIN_BODY_PRIME and prime not in FLOW_PRIMES
            ]
            for prime in primes:
                current = keyword_prime_weights.get(prime)
                if current is None or weight > current:
                    keyword_prime_weights[prime] = weight

        tokens = normalise_tokens(full_text)
        if not tokens:
            entry.state.metadata = metadata
            return []

        primes = self._token_index.primes_for_tokens(tokens)
        primes = [
            prime
            for prime in primes
            if prime >= MIN_BODY_PRIME and prime not in FLOW_PRIMES
        ]

        metadata["token_primes"] = primes
        try:
            self._token_index.update_inverted_index(primes, entry.key.as_path())
        except Exception:
            pass
        # Persist the prime lattice as an exponent vector so search can use
        # lattice operations (meet/join/orthogonality) without materialising
        # the full integer product.
        lattice = PrimeLatticeState.from_primes(primes)
        metadata["prime_lattice_exponents"] = dict(lattice.exponents)
        entry.state.metadata = metadata

        if keyword_prime_weights:
            try:
                self._token_index.update_keyword_index(keyword_prime_weights, entry.key.as_path())
            except Exception:
                pass

        return primes

    def summarize(self, namespace: str | None = None) -> dict[str, Any]:
        """
        Return lightweight metadata for entries in ``namespace`` using an
        efficient prefix scan over overlay records.
        """
        overlay_prefix = f"{self.OVERLAY_PREFIX}{namespace}:".encode() if namespace else f"{self.OVERLAY_PREFIX}".encode()

        total_entries = 0
        pinned_count = 0
        last_updated: datetime | None = None

        with self._lock:
            if not hasattr(self._db, "iterkeys"):
                for raw_key, raw_value in self._db.items():
                    key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
                    if not key_bytes.startswith(overlay_prefix):
                        continue
                    total_entries += 1
                    try:
                        payload = json.loads(raw_value)
                        if bool(payload.get("pinned")):
                            pinned_count += 1
                        created_at_raw = payload.get("created_at")
                        if created_at_raw:
                            created_at = datetime.fromisoformat(created_at_raw)
                            if last_updated is None or created_at > last_updated:
                                last_updated = created_at
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
            else:
                keys_iterator = self._db.iterkeys()  # type: ignore[attr-defined]
                keys_iterator.seek(overlay_prefix)
                for raw_key in keys_iterator:
                    key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
                    if not key_bytes.startswith(overlay_prefix):
                        break

                    raw_value = self._db.get(key_bytes)
                    if raw_value is None:
                        continue

                    total_entries += 1
                    try:
                        payload = json.loads(raw_value)
                        if bool(payload.get("pinned")):
                            pinned_count += 1
                        created_at_raw = payload.get("created_at")
                        if created_at_raw:
                            created_at = datetime.fromisoformat(created_at_raw)
                            if last_updated is None or created_at > last_updated:
                                last_updated = created_at
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue

        return {
            "total_entries": total_entries,
            "pinned_count": pinned_count,
            "last_updated": last_updated.isoformat() if last_updated else None,
        }
