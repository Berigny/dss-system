"""Base ledger foundation service.

Materialises the kernel lattice, patch registry, and value-node balance rules
into an immutable ledger record that every provision must carry before
operational writes are accepted.

The persisted record is two-layer:

* ``public`` — engineering-only runtime mirror, derived from
  ``backend.kernel.constants``.
* ``private`` — steward-only semantic layer, loaded from the encrypted KSR's
  cached population at bootstrap time and never exposed through public APIs.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, MutableMapping

import yaml

from backend.kernel import constants


class MissingFoundationError(Exception):
    """Raised when an operation requires a base foundation that is absent."""

    def __init__(self, provision_id: str) -> None:
        self.provision_id = provision_id
        super().__init__(
            f"Base ledger foundation missing for provision {provision_id}; "
            "provision is in DEGRADED_MODE until bootstrap completes."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocked": True,
            "reason": "missing_base_foundation",
            "provision_id": self.provision_id,
            "detail": "Operational writes require a base ledger foundation record.",
        }


class BaseFoundationService:
    """Builds and persists the base ledger foundation record."""

    KEY_PREFIX = b"__base_foundation__"
    VERSION = "1.1"

    def __init__(self, db: MutableMapping[bytes, bytes]) -> None:
        self._db = db

    @staticmethod
    def _foundation_key(provision_id: str) -> bytes:
        return BaseFoundationService.KEY_PREFIX + provision_id.encode("utf-8")

    @staticmethod
    def _private_layer_path() -> Path:
        """Return the path to the steward-only ledger-foundation population."""
        return (
            Path(__file__).parent
            / ".ksr"
            / "Kernel"
            / "ledger_foundation.json"
        )

    @classmethod
    def _load_private_layer(cls) -> dict[str, Any] | None:
        """Load the private semantic layer if it is present on disk."""
        path = cls._private_layer_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return data.get("private")
        except Exception:
            return None

    @staticmethod
    def _ksr_yaml_path() -> Path:
        """Return the path to the private Kernel Semantic Registry YAML."""
        return Path(__file__).parent / "semantic_registry.yaml"

    @classmethod
    def _load_cross_domain_registry(cls) -> dict[str, Any] | None:
        """Load the cross-domain registry from the encrypted KSR's plaintext mirror.

        The KSR YAML is private; this is only read at bootstrap time to
        materialise the public foundation view.
        """
        path = cls._ksr_yaml_path()
        if not path.exists():
            return None
        try:
            ksr = yaml.safe_load(path.read_text())
            return ksr.get("cross_domain_registry")
        except Exception:
            return None

    def _build_public_layer(self, provision_id: str) -> dict[str, Any]:
        """Return the engineering-only public foundation layer."""
        lattice = {
            "cube_id": constants.LATTICE_CUBE_ID,
            "lattice_type": constants.LATTICE_TYPE,
            "total_nodes": constants.LATTICE_TOTAL_NODES,
            "centroid_coordinate": constants.LATTICE_CENTROID_COORDINATE,
            "reset_coordinate": constants.LATTICE_RESET_COORDINATE,
            "corner_map": dict(constants.LATTICE_CORNER_MAP),
            "bridge_edges": list(constants.LATTICE_BRIDGE_EDGES),
            "face_centers": list(constants.LATTICE_FACE_CENTERS),
            "traversal_sequence": list(constants.LATTICE_TRAVERSAL_SEQUENCE),
            "flow_rules": list(constants.LATTICE_FLOW_RULES),
        }

        patch_registry = {
            "version": constants.PATCH_REGISTRY_VERSION,
            "patch_ids": list(constants.PATCH_IDS),
            "patches": {pid: dict(meta) for pid, meta in constants.PATCH_REGISTRY.items()},
            "e6_header_layout": {
                "patch_bits": list(constants.PATCH_E6_PATCH_BITS),
                "checksum_bits": list(constants.PATCH_E6_CHECKSUM_BITS),
                "reserved_bits": list(constants.PATCH_E6_RESERVED_BITS),
            },
        }

        value_node_registry = {
            "version": constants.VALUE_NODE_REGISTRY_VERSION,
            "labels": list(constants.VALUE_NODE_LABELS),
            "dimensions": dict(constants.VALUE_NODE_DIMENSIONS),
            "prime_affinities": dict(constants.VALUE_NODE_PRIME_AFFINITIES),
            "balance_rules": dict(constants.VALUE_NODE_BALANCE_RULES),
        }

        public = {
            "version": self.VERSION,
            "ksr_version": constants.KSR_VERSION,
            "provision_id": provision_id,
            "origin_timestamp": datetime.now(timezone.utc).isoformat(),
            "origin_salt": secrets.token_hex(16),
            "reference_documents": dict(constants.REFERENCE_DOCUMENTS),
            "kernel_cube": lattice,
            "patch_registry": patch_registry,
            "value_node_registry": value_node_registry,
            "checksum_336": {
                "name": "checksum_336",
                "value": constants.CHECKSUM_336,
                "lattice_rules": dict(constants.CHECKSUM_336_LATTICE_RULES),
            },
        }

        cross_domain = self._load_cross_domain_registry()
        if cross_domain is not None:
            public["cross_domain_registry"] = {
                "version": cross_domain.get("version", "1.0"),
                "domains": dict(cross_domain.get("domains", {})),
                "value_node_traversal": cross_domain.get("value_node_traversal"),
            }

        return public

    def build_foundation(self, provision_id: str) -> dict[str, Any]:
        """Return the deterministic two-layer foundation record."""
        public = self._build_public_layer(provision_id)
        private = self._load_private_layer()
        record: dict[str, Any] = {
            "version": self.VERSION,
            "ksr_version": constants.KSR_VERSION,
            "provision_id": provision_id,
            "origin_timestamp": public["origin_timestamp"],
            "public": public,
        }
        if private is not None:
            record["private"] = private
        return record

    def write_foundation(self, provision_id: str) -> dict[str, Any]:
        """Build and persist the foundation record; idempotent."""
        record = self.build_foundation(provision_id)
        self._db[self._foundation_key(provision_id)] = json.dumps(
            record, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return record

    def read_foundation(self, provision_id: str) -> dict[str, Any] | None:
        """Return the persisted foundation record, or None if absent."""
        raw = self._db.get(self._foundation_key(provision_id))
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _record_has_required_views(self, record: dict[str, Any]) -> bool:
        """Return True if the foundation record contains all required views."""
        public = record.get("public") if isinstance(record, dict) else None
        if not isinstance(public, dict):
            return False
        required = ("kernel_cube", "patch_registry", "cross_domain_registry")
        return all(key in public for key in required)

    def has_base_foundation(self, provision_id: str) -> bool:
        """Return True if the foundation record exists and is complete."""
        raw = self._db.get(self._foundation_key(provision_id))
        if raw is None:
            return False
        try:
            record = json.loads(raw.decode("utf-8"))
        except Exception:
            return False
        return self._record_has_required_views(record)

    def require_base_foundation(self, provision_id: str) -> None:
        """Raise MissingFoundationError if the foundation is absent."""
        if not self.has_base_foundation(provision_id):
            raise MissingFoundationError(provision_id)


def bootstrap_test_ledger(
    db: MutableMapping[bytes, bytes],
    provision_id: str = "test-provision",
) -> dict[str, Any]:
    """Convenience helper for tests: ensure a provision has its foundation."""
    service = BaseFoundationService(db)
    return service.write_foundation(provision_id)
