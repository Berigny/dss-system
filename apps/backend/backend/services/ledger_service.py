"""Ledger service boundary for API routes and sync handlers."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Iterable, MutableMapping

from fastapi import HTTPException, Request

from backend.fieldx_kernel.ledger import MemoryLedger
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate import LedgerStoreV2, MemorySubstrate
from backend.kernel.base_foundation import BaseFoundationService, MissingFoundationError
from backend.kernel import constants
from backend.kernel.hysteresis_proof import HysteresisProof
from backend.kernel.rocksdb_layer_store import RocksDBLayerStore
from backend.metrics.store import TelemetryStore
from backend.search.token_index import TokenPrimeIndex

REQUIRE_BASE_FOUNDATION = os.getenv("DSS_REQUIRE_BASE_FOUNDATION", "1") == "1"

LEDGER_REGISTRY_V1_KEY = b"__ledgers_v1__"


def _canonicalize_ledger_id(value: str) -> str:
    text = str(value or "").strip()
    while text.startswith("ledger:"):
        text = text[len("ledger:") :].strip()
    return text


class LedgerService:
    """Thin service wrapper around ledger persistence and sync metadata IO."""

    def __init__(
        self,
        db: MutableMapping[Any, Any],
        *,
        token_index: TokenPrimeIndex | None = None,
        provision_id: str = "default",
    ):
        self._db = db
        self._store = LedgerStoreV2(db, token_index=token_index)
        self._layer_store = RocksDBLayerStore(db, provision_id=provision_id)

    @property
    def store(self) -> LedgerStoreV2:
        return self._store

    @property
    def layer_store(self) -> RocksDBLayerStore:
        return self._layer_store

    @property
    def db(self) -> MutableMapping[Any, Any]:
        return self._db

    def memory_ledger(self) -> MemoryLedger:
        return MemoryLedger(self._db)

    def memory_substrate(self) -> MemorySubstrate:
        return MemorySubstrate(self._db)

    def telemetry_store(self) -> TelemetryStore:
        return TelemetryStore(self._db)

    @classmethod
    def from_request(cls, request: Request, *, with_token_index: bool = False) -> "LedgerService":
        db = getattr(request.app.state, "db", None)
        if db is None:
            raise HTTPException(status_code=503, detail="Database not initialized")
        token_index = TokenPrimeIndex(request.app) if with_token_index else None
        return cls(db, token_index=token_index)

    def ensure_base_foundation(self, provision_id: str) -> dict[str, Any]:
        """Bootstrap the base ledger foundation record for ``provision_id``."""
        return BaseFoundationService(self._db).write_foundation(provision_id)

    def write_entry(self, entry: LedgerEntry) -> None:
        if REQUIRE_BASE_FOUNDATION:
            BaseFoundationService(self._db).require_base_foundation(entry.key.namespace)
        self._store.write(entry)

    def append_process(
        self,
        slots: dict[str, str],
        *,
        v_values: tuple[int, int, int] = (3, 3, 3),
    ) -> dict[str, Any]:
        """Encode a process and persist it as a Loam layer entry.

        This is the integration point between the dual-layer ledger
        (dss_ledger) and the existing geological layer store. The process
        itself is validated by the causal graph; the layer entry carries the
        PID as metadata for retrieval.
        """
        from dss_ledger.service import ProcessService

        service = ProcessService()
        encoded = service.encode(slots)
        entry = {
            "layer": constants.LAYER_LOAM,
            "coord": encoded["canonical"],
            "v_awareness": v_values[0],
            "v_unity": v_values[1],
            "v_ethics": v_values[2],
            "value": encoded["canonical"],
            "pid": encoded["pid"],
        }
        layer = self.write_layer_entry(entry)
        return {
            "pid": encoded["pid"],
            "canonical": encoded["canonical"],
            "layer": layer,
        }

    def write_layer_entry(self, entry: dict[str, Any]) -> str:
        """Write a quaternary-gate routed entry to the geological layer store."""
        if REQUIRE_BASE_FOUNDATION:
            BaseFoundationService(self._db).require_base_foundation(
                entry.get("namespace", "default")
            )

        routed_layer = entry.get("layer") or self._layer_store._router.route(entry)
        if routed_layer == constants.LAYER_CLAY:
            elevation_bundle = entry.get("elevation_bundle", {})
            proposed_block = int(entry.get("block_height", 0))
            clay_ledger = self._layer_store.clay_ledger()
            # Genesis Clay (empty ledger) is exempt from hysteresis.
            if clay_ledger and not HysteresisProof.is_valid_for_elevation(
                elevation_bundle.get("hysteresis_proof"),
                clay_ledger,
                proposed_block,
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Clay elevation rejected: invalid or missing hysteresis proof",
                )

        return self._layer_store.write(entry)

    def write_raw_entry(self, raw: dict[str, Any]) -> bool:
        """Best-effort compatibility write for legacy sync payloads."""
        key_data = raw.get("key", {})
        state_data = raw.get("state", {})
        entry = LedgerEntry(
            key=LedgerKey(
                namespace=key_data.get("namespace"),
                identifier=key_data.get("identifier"),
            ),
            state=ContinuousState(
                coordinates=state_data.get("coordinates", {}),
                phase=state_data.get("phase"),
                metadata=state_data.get("metadata", {}),
            ),
            created_at=datetime.fromisoformat(raw["created_at"]),
            notes=raw.get("notes"),
            pinned=raw.get("pinned", False),
        )
        self.write_entry(entry)
        return True

    @staticmethod
    def to_bytes_key(text: str) -> bytes:
        return text.encode("utf-8")

    @staticmethod
    def json_loads(raw: Any) -> dict[str, Any] | None:
        if raw is None:
            return None
        try:
            payload = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
            loaded = json.loads(payload)
            return loaded if isinstance(loaded, dict) else None
        except Exception:
            return None

    @staticmethod
    def json_dumps(data: dict[str, Any]) -> bytes:
        return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def set_json(self, key: bytes, data: dict[str, Any]) -> None:
        self._db[key] = self.json_dumps(data)

    def get_json(self, key: bytes) -> dict[str, Any] | None:
        return self.json_loads(self._db.get(key))

    def load_registered_ledgers_v1(self) -> dict[str, dict[str, Any]]:
        payload = self.get_json(LEDGER_REGISTRY_V1_KEY)
        rows = payload.get("ledgers", payload) if isinstance(payload, dict) else None
        if not isinstance(rows, dict):
            return {}
        registry: dict[str, dict[str, Any]] = {}
        for ledger_id, record in rows.items():
            raw_ledger_id = str(ledger_id).strip()
            normalized_id = _canonicalize_ledger_id(ledger_id)
            if not normalized_id or not isinstance(record, dict):
                continue
            normalized = dict(record)
            normalized["ledger_id"] = normalized_id
            existing = registry.get(normalized_id)
            if isinstance(existing, dict) and str(existing.get("ledger_id") or "").strip() == normalized_id and raw_ledger_id != normalized_id:
                existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
                incoming_metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
                merged_metadata = dict(existing_metadata)
                merged_metadata["ledger_alias_history"] = [
                    *(
                        existing_metadata.get("ledger_alias_history")
                        if isinstance(existing_metadata.get("ledger_alias_history"), list)
                        else []
                    ),
                    *(
                        incoming_metadata.get("ledger_alias_history")
                        if isinstance(incoming_metadata.get("ledger_alias_history"), list)
                        else []
                    ),
                ]
                existing["metadata"] = merged_metadata
                registry[normalized_id] = existing
                continue
            registry[normalized_id] = normalized
        return registry

    def get_registered_ledger_record(self, ledger_id: str) -> dict[str, Any] | None:
        normalized = _canonicalize_ledger_id(ledger_id)
        if not normalized:
            return None
        return self.load_registered_ledgers_v1().get(normalized)

    def resolve_canonical_ledger_id(self, ledger_id: str) -> str:
        normalized = _canonicalize_ledger_id(ledger_id)
        if not normalized:
            return normalized
        registry = self.load_registered_ledgers_v1()
        direct = registry.get(normalized) if isinstance(registry.get(normalized), dict) else None
        if isinstance(direct, dict):
            metadata = direct.get("metadata") if isinstance(direct.get("metadata"), dict) else {}
            superseded_by = str(
                metadata.get("superseded_by_ledger_id")
                or metadata.get("canonical_ledger_id")
                or ""
            ).strip()
            if superseded_by and superseded_by in registry:
                return superseded_by
            return normalized
        for candidate_id, record in registry.items():
            if not isinstance(record, dict):
                continue
            metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            namespace = _canonicalize_ledger_id(record.get("namespace") or "")
            aliases = metadata.get("ledger_alias_history") if isinstance(metadata.get("ledger_alias_history"), list) else []
            if normalized == namespace:
                return candidate_id
            if any(_canonicalize_ledger_id(item) == normalized for item in aliases if isinstance(item, str)):
                return candidate_id
        return normalized

    def resolve_registered_ledger_record(self, ledger_id: str) -> dict[str, Any] | None:
        canonical_id = self.resolve_canonical_ledger_id(ledger_id)
        return self.get_registered_ledger_record(canonical_id)

    def get_ledger_library_boundary(self, ledger_id: str) -> dict[str, Any]:
        normalized = _canonicalize_ledger_id(ledger_id)
        canonical_id = self.resolve_canonical_ledger_id(normalized)
        record = self.get_registered_ledger_record(canonical_id)
        metadata = record.get("metadata") if isinstance(record, dict) and isinstance(record.get("metadata"), dict) else {}
        founding = metadata.get("founding_constitution") if isinstance(metadata.get("founding_constitution"), dict) else {}
        display_name = str(
            (record or {}).get("display_name")
            or (record or {}).get("name")
            or (record or {}).get("ledger_id")
            or canonical_id
            or normalized
        ).strip()
        foundation_name = str(founding.get("name") or "").strip() or display_name or None
        foundation_personality = str(founding.get("personality") or "").strip() or None
        foundation_purpose = str(founding.get("purpose") or "").strip() or None
        foundation_source = str(founding.get("source") or "").strip() or None
        if foundation_source is None and any([foundation_personality, foundation_purpose]):
            foundation_source = "control_plane_operator"
        rehydration_mode = "absent"
        if foundation_name and (foundation_personality or foundation_purpose or foundation_source):
            rehydration_mode = "founding_constitution"
        elif foundation_name:
            rehydration_mode = "display_name_fallback"
        alias_history = metadata.get("ledger_alias_history") if isinstance(metadata.get("ledger_alias_history"), list) else []
        supersession_history = (
            metadata.get("ledger_supersession_history")
            if isinstance(metadata.get("ledger_supersession_history"), list)
            else []
        )
        consolidation_history = (
            metadata.get("ledger_consolidation_history")
            if isinstance(metadata.get("ledger_consolidation_history"), list)
            else []
        )
        canonical_runtime_ledger_id = str((record or {}).get("ledger_id") or canonical_id or normalized).strip() or normalized
        rename_log = [
            value
            for value in [str(item).strip() for item in alias_history if isinstance(item, str) and str(item).strip()]
            if value and value != canonical_runtime_ledger_id
        ][:12]
        continuity_basis: list[str] = []
        if foundation_name:
            continuity_basis.append("foundation_identity.name")
        if foundation_purpose:
            continuity_basis.append("foundation_identity.purpose")
        if foundation_source:
            continuity_basis.append("foundation_identity.source")
        if rename_log:
            continuity_basis.append("ledger_alias_history")
        if supersession_history:
            continuity_basis.append("ledger_supersession_history")
        if consolidation_history:
            continuity_basis.append("ledger_consolidation_history")
        foundation_identity_ref = None
        if rehydration_mode == "founding_constitution" and (foundation_name or foundation_purpose or foundation_source):
            foundation_identity_ref = f"ledger:{canonical_runtime_ledger_id}:foundation_identity"
        identity_continuity_witness = {
            "canonical_ledger_id": canonical_runtime_ledger_id,
            "basis": continuity_basis,
            "alias_history_count": len(rename_log),
            "supersession_history_count": len(
                [str(item).strip() for item in supersession_history if isinstance(item, str) and str(item).strip()]
            ),
            "consolidation_history_count": len([item for item in consolidation_history if item]),
            "foundation_identity_available": bool(foundation_name or foundation_purpose or foundation_source),
        }
        consolidation_events = [item for item in consolidation_history if isinstance(item, dict)]
        latest_consolidation = consolidation_events[-1] if consolidation_events else {}
        latest_consolidation_event = {
            "event": str(latest_consolidation.get("event") or "").strip() or None,
            "timestamp": str(latest_consolidation.get("timestamp") or "").strip() or None,
            "reason": str(latest_consolidation.get("reason") or "").strip() or None,
            "operator_principal_id": str(latest_consolidation.get("operator_principal_id") or "").strip() or None,
            "superseded_ledger_ids": [
                str(item).strip()
                for item in (
                    latest_consolidation.get("superseded_ledger_ids")
                    if isinstance(latest_consolidation.get("superseded_ledger_ids"), list)
                    else []
                )
                if isinstance(item, str) and str(item).strip()
            ],
        }
        latest_event_name = str(latest_consolidation_event.get("event") or "").strip()
        latest_event_ts = str(latest_consolidation_event.get("timestamp") or "").strip()
        latest_consolidation_event_id = (
            f"{canonical_runtime_ledger_id}:{latest_event_name}:{latest_event_ts}"
            if latest_event_name and latest_event_ts
            else None
        )
        ledger_version = (
            metadata.get("ledger_version")
            if isinstance(metadata.get("ledger_version"), int)
            else (1 + len(consolidation_events))
        )
        continuity_checkpoint = {
            "checkpoint_ref": latest_consolidation_event_id or f"{canonical_runtime_ledger_id}:registry",
            "checkpoint_updated_at": str((record or {}).get("updated_at") or latest_event_ts or "").strip() or None,
            "ledger_version": ledger_version,
            "consolidation_event_count": len(consolidation_events),
        }
        async_consolidation_state = str(metadata.get("async_consolidation_state") or "").strip() or (
            "settled_on_canonical_boundary" if consolidation_events else "idle_no_pending_consolidation"
        )
        settlement_boundary_ns = str(metadata.get("settlement_boundary_ns") or "").strip() or "bounded_async_only"
        canonical_subject = str((record or {}).get("canonical_subject") or "").strip() or None
        canonical_identity_post_consolidation = {
            "canonical_ledger_id": canonical_runtime_ledger_id,
            "canonical_subject": canonical_subject,
            "continuity_survived": bool(
                canonical_runtime_ledger_id
                and (
                    rename_log
                    or supersession_history
                    or consolidation_events
                    or foundation_name
                    or foundation_purpose
                    or foundation_source
                )
            ),
            "latest_consolidation_event_id": latest_consolidation_event_id,
        }
        return {
            "canonical_ledger_id": canonical_runtime_ledger_id,
            "requested_ledger_id": normalized or None,
            "alias_resolution_applied": canonical_id != normalized,
            "registry_source": "registered_ledger_v1" if isinstance(record, dict) else "runtime_default",
            "river_reads_policy_bounded": True,
            "river_mutates_library_directly": False,
            "hot_path_mode": "summary_only",
            "latency_boundary": {
                "hot_path_budgeted": True,
                "deep_history_requires_fallback_or_deferral": True,
                "interactive_path": "summary_only_or_skip",
                "deeper_replay_requires": "fallback_or_deferral",
                "settlement_boundary_ns": settlement_boundary_ns,
            },
            "foundation_identity": {
                "name": foundation_name,
                "personality": foundation_personality,
                "purpose": foundation_purpose,
                "source": foundation_source,
                "rehydration_mode": rehydration_mode,
                "constitution_present": bool(founding),
                "foundation_identity_ref": foundation_identity_ref,
            },
            "history_continuity": {
                "alias_aware_coord_history_lookup": True,
                "surviving_governed_memory_boundary": canonical_runtime_ledger_id,
                "foundation_identity_available_after_consolidation": bool(foundation_name or foundation_purpose or foundation_source),
                "full_available_history_visible_across_aliases": True,
            },
            "alias_history": [str(item).strip() for item in alias_history if isinstance(item, str) and str(item).strip()][:12],
            "supersession_history": [str(item).strip() for item in supersession_history if isinstance(item, str) and str(item).strip()][:12],
            "consolidation_history_count": len([item for item in consolidation_history if item]),
            "latest_consolidation_event": latest_consolidation_event,
            "latest_consolidation_event_id": latest_consolidation_event_id,
            "continuity_checkpoint": continuity_checkpoint,
            "ledger_version": ledger_version,
            "async_consolidation_state": async_consolidation_state,
            "canonical_identity_post_consolidation": canonical_identity_post_consolidation,
            "identity_continuity_witness": identity_continuity_witness,
            "ledger_rename_log": rename_log,
        }

    def raw_get(self, key: bytes) -> Any:
        return self._db.get(key)

    def raw_set(self, key: bytes, value: bytes) -> None:
        self._db[key] = value

    def iter_prefix_keys(self, prefix: bytes) -> Iterable[bytes]:
        if hasattr(self._db, "iterkeys"):
            iterator = self._db.iterkeys()  # type: ignore[attr-defined]
            iterator.seek(prefix)
            for raw_key in iterator:
                key = raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key
                if not key.startswith(prefix):
                    break
                yield key
            return

        for raw_key in self._db.keys():
            key = raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key
            if key.startswith(prefix):
                yield key
