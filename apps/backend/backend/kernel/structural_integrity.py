"""Structural integrity dead man's switch for the Kernel Semantic Registry.

The protocol decrypts and verifies the KSR envelope at startup. If any
verification step fails, the system enters ``DEGRADED_MODE``: operational
writes are refused, but read-only diagnostics remain available.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from .ksr_crypto import (
    KsrAuthenticationError,
    KsrEnvelope,
    KsrIntegrityError,
    decrypt_ksr,
)


class IntegrityState(str, Enum):
    INIT = "INIT"
    VERIFYING = "VERIFYING"
    HEALTHY = "HEALTHY"
    DEGRADED_MODE = "DEGRADED_MODE"
    RECOVERING = "RECOVERING"


class IntegrityFailureReason(str, Enum):
    MISSING_ENVELOPE = "missing_envelope"
    AUTHENTICATION_FAILURE = "authentication_failure"
    KSR_HASH_MISMATCH = "ksr_hash_mismatch"
    GLOSSARY_HASH_MISMATCH = "glossary_hash_mismatch"
    WHITEPAPER_HASH_MISMATCH = "whitepaper_hash_mismatch"
    CONSTANT_DERIVATION_FAILURE = "constant_derivation_failure"
    NO_PASSWORD = "no_password"


class StructuralIntegrityError(Exception):
    """Raised when an operation is attempted while the protocol is degraded."""


@dataclass
class IntegrityDiagnostic:
    timestamp: str
    state: str
    reason: str | None
    detail: str


@dataclass
class IntegrityStatus:
    state: IntegrityState
    reason: IntegrityFailureReason | None = None
    detail: str = ""
    diagnostics: list[IntegrityDiagnostic] = field(default_factory=list)
    ksr_hash_match: bool | None = None
    glossary_hash_match: bool | None = None
    whitepaper_hash_match: bool | None = None
    constants_match: bool | None = None


class StructuralIntegrityProtocol:
    """Verifies KSR integrity and gates operational writes."""

    def __init__(
        self,
        repo_root: str | Path | None = None,
        password: str | None = None,
        ledger_path: str | Path | None = None,
    ) -> None:
        if repo_root is None:
            repo_root = Path(__file__).parent.parent.parent
        self.repo_root = Path(repo_root)
        self.kernel_dir = self.repo_root / "backend" / "kernel"
        self.ksr_dir = self.kernel_dir / ".ksr"
        self.envelope_path = self.kernel_dir / "semantic_registry.enc"
        self.password = password or os.getenv("DSS_KSR_PASSWORD")
        self._ledger_path = Path(ledger_path) if ledger_path else self.ksr_dir / "integrity.log"
        self._state = IntegrityState.INIT
        self._status = IntegrityStatus(state=IntegrityState.INIT)
        self._ksr_plaintext: bytes | None = None
        self._ksr_data: dict[str, Any] | None = None

    @property
    def state(self) -> IntegrityState:
        return self._state

    @property
    def status(self) -> IntegrityStatus:
        return self._status

    def _read_expected_hash(self, name: str) -> str:
        return (self.ksr_dir / f"{name}.hash").read_text().strip()

    def _append_diagnostic(self, reason: IntegrityFailureReason | None, detail: str) -> None:
        diagnostic = IntegrityDiagnostic(
            timestamp=datetime.now(timezone.utc).isoformat(),
            state=self._state.value,
            reason=reason.value if reason else None,
            detail=detail,
        )
        self._status.diagnostics.append(diagnostic)
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self._ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": diagnostic.timestamp,
                "state": diagnostic.state,
                "reason": diagnostic.reason,
                "detail": diagnostic.detail,
            }) + "\n")

    def _set_degraded(
        self,
        reason: IntegrityFailureReason,
        detail: str,
    ) -> None:
        self._state = IntegrityState.DEGRADED_MODE
        self._status.state = IntegrityState.DEGRADED_MODE
        self._status.reason = reason
        self._status.detail = detail
        self._append_diagnostic(reason, detail)

    def _verify_hashes(self, plaintext: bytes) -> tuple[bool, bool, bool]:
        ksr_hash = hashlib.sha256(plaintext).hexdigest()
        ksr_match = ksr_hash == self._read_expected_hash("ksr")

        data = yaml.safe_load(plaintext)
        self._ksr_data = data
        glossary_canon = json.dumps(data.get("glossary", []), sort_keys=True, ensure_ascii=False)
        glossary_hash = hashlib.sha256(glossary_canon.encode("utf-8")).hexdigest()
        glossary_match = glossary_hash == self._read_expected_hash("glossary")

        whitepaper_hash = data.get("reference_documents", {}).get("whitepaper", {}).get("sha256", "")
        whitepaper_match = whitepaper_hash == self._read_expected_hash("whitepaper")

        return ksr_match, glossary_match, whitepaper_match

    def initialize(self) -> IntegrityStatus:
        """Run the full integrity verification and enter HEALTHY or DEGRADED_MODE."""
        self._state = IntegrityState.VERIFYING
        self._status.state = IntegrityState.VERIFYING

        if not self.envelope_path.exists():
            self._set_degraded(
                IntegrityFailureReason.MISSING_ENVELOPE,
                f"KSR envelope not found: {self.envelope_path}",
            )
            return self._status

        if not self.password:
            self._set_degraded(
                IntegrityFailureReason.NO_PASSWORD,
                "DSS_KSR_PASSWORD is not set; cannot authenticate KSR envelope.",
            )
            return self._status

        try:
            envelope = KsrEnvelope.read(self.envelope_path)
        except Exception as exc:
            self._set_degraded(
                IntegrityFailureReason.AUTHENTICATION_FAILURE,
                f"Failed to read KSR envelope: {exc}",
            )
            return self._status

        try:
            expected_ksr_hash = self._read_expected_hash("ksr")
            expected_whitepaper_hash = self._read_expected_hash("whitepaper")
            plaintext = decrypt_ksr(
                envelope,
                self.password,
                whitepaper_hash=expected_whitepaper_hash,
                expected_sha256=expected_ksr_hash,
            )
        except KsrAuthenticationError as exc:
            self._set_degraded(
                IntegrityFailureReason.AUTHENTICATION_FAILURE,
                f"KSR authentication failed: {exc}",
            )
            return self._status
        except KsrIntegrityError as exc:
            self._set_degraded(
                IntegrityFailureReason.KSR_HASH_MISMATCH,
                f"KSR content hash mismatch: {exc}",
            )
            return self._status
        except Exception as exc:
            self._set_degraded(
                IntegrityFailureReason.AUTHENTICATION_FAILURE,
                f"Unexpected KSR decryption error: {exc}",
            )
            return self._status

        self._ksr_plaintext = plaintext
        ksr_match, glossary_match, whitepaper_match = self._verify_hashes(plaintext)
        self._status.ksr_hash_match = ksr_match
        self._status.glossary_hash_match = glossary_match
        self._status.whitepaper_hash_match = whitepaper_match

        if not ksr_match:
            self._set_degraded(
                IntegrityFailureReason.KSR_HASH_MISMATCH,
                "Decrypted KSR hash does not match expected hash.",
            )
            return self._status
        if not glossary_match:
            self._set_degraded(
                IntegrityFailureReason.GLOSSARY_HASH_MISMATCH,
                "KSR glossary hash does not match expected hash.",
            )
            return self._status
        if not whitepaper_match:
            self._set_degraded(
                IntegrityFailureReason.WHITEPAPER_HASH_MISMATCH,
                "KSR whitepaper hash does not match expected hash.",
            )
            return self._status

        self._state = IntegrityState.HEALTHY
        self._status.state = IntegrityState.HEALTHY
        self._status.reason = None
        self._status.detail = "KSR integrity verified."
        self._append_diagnostic(None, "Integrity verification succeeded.")
        return self._status

    def require_healthy(self, operation: str = "write") -> None:
        """Raise if operational writes are not permitted.

        Call sites that perform ledger/memory/context writes should invoke this
        method before committing state changes.
        """
        if self._state != IntegrityState.HEALTHY:
            raise StructuralIntegrityError(
                f"Structural integrity is {self._state.value}; "
                f"{operation} is not permitted in DEGRADED_MODE."
            )

    def can_write(self) -> bool:
        """Return True iff the system is healthy enough to accept writes."""
        return self._state == IntegrityState.HEALTHY

    def attempt_recovery(self, password: str | None = None) -> IntegrityStatus:
        """Attempt to re-verify integrity, optionally with a new password."""
        self._state = IntegrityState.RECOVERING
        self._status.state = IntegrityState.RECOVERING
        if password:
            self.password = password
        self._status = IntegrityStatus(state=IntegrityState.RECOVERING)
        return self.initialize()

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "reason": self._status.reason.value if self._status.reason else None,
            "detail": self._status.detail,
            "ksr_hash_match": self._status.ksr_hash_match,
            "glossary_hash_match": self._status.glossary_hash_match,
            "whitepaper_hash_match": self._status.whitepaper_hash_match,
            "constants_match": self._status.constants_match,
        }
