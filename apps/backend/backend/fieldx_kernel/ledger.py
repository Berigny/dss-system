"""Helper accessors for S1/S2 ledger state without flow enforcement."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from typing import Any, Dict, MutableMapping

from backend.fieldx_kernel.state import default_S1, default_S2, default_mediators


_LEDGER_LOCK = RLock()
_ALLOW_MEDIATOR_WRITES: ContextVar[bool] = ContextVar("allow_mediator_writes", default=False)


def _encode_key(key: str) -> bytes:
    return key.encode()


class MemoryLedger:
    """Persist and retrieve S1/S2 state directly from the substrate store."""

    def __init__(self, db: MutableMapping[bytes, bytes]):
        self._db = db

    def _load(self, key: str, default: Dict[str, dict]) -> Dict[str, dict]:
        encoded_key = _encode_key(key)
        with _LEDGER_LOCK:
            raw = self._db.get(encoded_key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return default

    def _store(self, key: str, value: Dict[str, dict]) -> Dict[str, dict]:
        encoded_key = _encode_key(key)
        with _LEDGER_LOCK:
            self._db[encoded_key] = json.dumps(value).encode()
        return value

    def _load_overlays(self, key: str) -> list[dict[str, Any]]:
        encoded_key = _encode_key(key)
        with _LEDGER_LOCK:
            raw = self._db.get(encoded_key)
        if raw is None:
            return []
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _store_overlays(self, key: str, overlays: list[dict[str, Any]]) -> list[dict[str, Any]]:
        encoded_key = _encode_key(key)
        with _LEDGER_LOCK:
            if overlays:
                self._db[encoded_key] = json.dumps(overlays).encode()
            else:
                try:
                    del self._db[encoded_key]
                except KeyError:
                    pass
        return overlays

    @staticmethod
    def _s2_base_key(entity: str) -> str:
        return f"entity:{entity}:s2"

    @staticmethod
    def _s2_overlay_key(entity: str) -> str:
        return f"entity:{entity}:s2:overlays"

    @staticmethod
    def _validate_s1_state(state: Dict[str, dict]) -> Dict[str, dict]:
        expected = default_S1()

        for prime, template in expected.items():
            slot = state.setdefault(prime, {"refs": [], "metadata": {}})
            slot.setdefault("refs", [])
            if not isinstance(slot["refs"], list):
                slot["refs"] = []
            slot.setdefault("metadata", {})
            if not isinstance(slot["metadata"], dict):
                slot["metadata"] = {}

        return state

    @staticmethod
    def _validate_s2_state(state: Dict[str, dict]) -> Dict[str, dict]:
        expected = default_S2()

        for prime, template in expected.items():
            slot = state.setdefault(prime, json.loads(json.dumps(template)))
            slot.setdefault("metadata", {})
            if not isinstance(slot["metadata"], dict):
                slot["metadata"] = {}

            for list_key in ("taxonomy", "linkmap", "claims"):
                if list_key in template:
                    slot.setdefault(list_key, [])
                    if not isinstance(slot[list_key], list):
                        slot[list_key] = []

            if "summary_ref" in template and "summary_ref" not in slot:
                slot["summary_ref"] = None

        return state

    @staticmethod
    def _validate_mediator_state(state: Dict[str, dict]) -> Dict[str, dict]:
        expected = default_mediators()

        for prime in expected:
            slot = state.setdefault(prime, {"metadata": {}})
            slot.setdefault("metadata", {})
            if not isinstance(slot["metadata"], dict):
                slot["metadata"] = {}

        return state

    def get_S1(self, entity: str) -> Dict[str, dict]:
        state = self._load(f"entity:{entity}:s1", default_S1())
        return self._validate_s1_state(state)

    def get_S2(self, entity: str) -> Dict[str, dict]:
        state = self._load(self._s2_base_key(entity), default_S2())
        state = self._validate_s2_state(state)
        overlays = self._load_overlays(self._s2_overlay_key(entity))
        materialized = json.loads(json.dumps(state))
        for overlay in overlays:
            if not isinstance(overlay, dict):
                continue
            updates = overlay.get("updates")
            if overlay.get("kind") != "replace_s2_v1" or not isinstance(updates, dict):
                continue
            self._apply_replace_s2_patch(materialized, updates)
        return self._validate_s2_state(materialized)

    def _get_s2_slot(self, entity: str, prime: str) -> dict:
        state = self.get_S2(entity)
        slot = state.get(str(prime), {})
        return slot if isinstance(slot, dict) else {}

    def _get_s1_values(self, entity: str) -> list[dict]:
        state = self._load(f"entity:{entity}:s1", default_S1())
        return [value for value in state.values() if isinstance(value, dict)]

    def get_s2_metadata(self, entity: str, prime: str = "11") -> dict:
        slot = self._get_s2_slot(entity, prime)
        metadata = slot.get("metadata", {})
        return metadata if isinstance(metadata, dict) else {}

    def get_s2_claims(
        self,
        entity: str,
        *,
        prime: str = "19",
        limit: int | None = None,
    ) -> list[dict]:
        slot = self._get_s2_slot(entity, prime)
        claims = slot.get("claims", [])
        if not isinstance(claims, list):
            return []
        limited = claims[:limit] if isinstance(limit, int) else claims
        return [claim for claim in limited if isinstance(claim, dict)]

    def get_s2_summary_ref(self, entity: str, prime: str = "11") -> int | str | None:
        slot = self._get_s2_slot(entity, prime)
        return slot.get("summary_ref")

    def get_s1_recent(self, entity: str, *, limit: int | None = None) -> list[dict]:
        values = self._get_s1_values(entity)
        return values[:limit] if isinstance(limit, int) else values

    def get_mediators(self, entity: str) -> Dict[str, dict]:
        state = self._load(f"entity:{entity}:mediators", default_mediators())
        return self._validate_mediator_state(state)

    def update_S1(self, entity: str, updates: Dict[str, dict]) -> Dict[str, dict]:
        current = self.get_S1(entity)

        for prime, patch in updates.items():
            prime_key = str(prime)
            slot = current.setdefault(prime_key, {"refs": [], "metadata": {}})

            refs = patch.get("refs")
            if isinstance(refs, list):
                slot.setdefault("refs", [])
                slot["refs"].extend(refs)

            metadata = patch.get("metadata")
            if isinstance(metadata, dict):
                existing_meta = slot.setdefault("metadata", {})
                existing_meta.update(metadata)

        return self._store(f"entity:{entity}:s1", current)

    def update_S2(self, entity: str, updates: Dict[str, dict]) -> Dict[str, dict]:
        current = self.get_S2(entity)

        for prime, patch in updates.items():
            prime_key = str(prime)
            slot = current.setdefault(prime_key, {})

            for key, value in patch.items():
                if key in {"taxonomy", "linkmap", "claims"}:
                    slot.setdefault(key, [])
                    if isinstance(value, list):
                        slot[key].extend(value)
                elif key == "summary_ref":
                    slot["summary_ref"] = value
                elif key == "metadata" and isinstance(value, dict):
                    slot.setdefault("metadata", {})
                    slot["metadata"].update(value)
                else:
                    slot[key] = value

        validated = self._validate_s2_state(current)
        stored = self._store(self._s2_base_key(entity), validated)
        # Compaction: base now reflects materialized S2 state, so overlays can be cleared.
        self._store_overlays(self._s2_overlay_key(entity), [])
        return stored

    @staticmethod
    def _apply_replace_s2_patch(current: Dict[str, dict], updates: Dict[str, dict]) -> Dict[str, dict]:
        for prime, patch in updates.items():
            prime_key = str(prime)
            slot = current.setdefault(prime_key, {})

            for key, value in patch.items():
                if key in {"taxonomy", "linkmap", "claims"}:
                    slot[key] = list(value) if isinstance(value, list) else []
                elif key == "summary_ref":
                    slot["summary_ref"] = value
                elif key == "metadata" and isinstance(value, dict):
                    slot.setdefault("metadata", {})
                    slot["metadata"].update(value)
                else:
                    slot[key] = value
        return current

    def replace_S2(self, entity: str, updates: Dict[str, dict]) -> Dict[str, dict]:
        """Apply destructive S2 list replacement as append-only overlay events."""
        current = self.get_S2(entity)
        next_state = self._apply_replace_s2_patch(json.loads(json.dumps(current)), updates)
        validated = self._validate_s2_state(next_state)

        overlays = self._load_overlays(self._s2_overlay_key(entity))
        seq = len(overlays) + 1
        overlay_event = {
            "event_id": f"s2ov-{seq}",
            "seq": seq,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "kind": "replace_s2_v1",
            "derived_from": self._s2_base_key(entity),
            "updates": updates,
        }
        overlays.append(overlay_event)
        self._store_overlays(self._s2_overlay_key(entity), overlays)
        return validated

    def update_mediators(self, entity: str, updates: Dict[str, dict]) -> Dict[str, dict]:
        if not _ALLOW_MEDIATOR_WRITES.get():
            raise PermissionError("Mediator writes are guardian-only")

        current = self.get_mediators(entity)

        for prime, patch in updates.items():
            prime_key = str(prime)
            slot = current.setdefault(prime_key, {"metadata": {}})

            metadata = patch.get("metadata")
            if isinstance(metadata, dict):
                slot.setdefault("metadata", {})
                slot["metadata"].update(metadata)

        validated = self._validate_mediator_state(current)
        return self._store(f"entity:{entity}:mediators", validated)


@contextmanager
def allow_mediator_writes():
    token = _ALLOW_MEDIATOR_WRITES.set(True)
    try:
        yield
    finally:
        _ALLOW_MEDIATOR_WRITES.reset(token)


__all__ = [
    "MemoryLedger",
    "allow_mediator_writes",
]
