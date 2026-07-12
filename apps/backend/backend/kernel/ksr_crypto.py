"""AES-256-GCM encryption for the Kernel Semantic Registry (KSR).

The KSR YAML is the private steward source of truth. At build time it is
encrypted into `backend/kernel/semantic_registry.enc`. Runtime code never
holds the decryption password; only build, test, and steward tooling decrypts
the envelope to generate `backend/kernel/constants.py` or to run integrity
checks.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend


class KsrAuthenticationError(Exception):
    """Raised when the encrypted KSR fails GCM authentication."""


class KsrIntegrityError(Exception):
    """Raised when the decrypted KSR does not match the expected content hash."""


# Default number of PBKDF2 iterations. Production and CI should override via
# DSS_KSR_PBKDF2_ITERATIONS. Tests use a low value for speed.
DEFAULT_PBKDF2_ITERATIONS = int(os.getenv("DSS_KSR_PBKDF2_ITERATIONS", "480000"))


@dataclass
class KsrEnvelope:
    """In-memory representation of the encrypted KSR envelope."""

    version: int
    salt: bytes
    nonce: bytes
    ciphertext: bytes
    tag: bytes
    ksr_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "salt": base64.b64encode(self.salt).decode("ascii"),
            "nonce": base64.b64encode(self.nonce).decode("ascii"),
            "tag": base64.b64encode(self.tag).decode("ascii"),
            "ciphertext": base64.b64encode(self.ciphertext).decode("ascii"),
            "ksr_sha256": self.ksr_sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KsrEnvelope":
        return cls(
            version=int(data["version"]),
            salt=base64.b64decode(data["salt"]),
            nonce=base64.b64decode(data["nonce"]),
            ciphertext=base64.b64decode(data["ciphertext"]),
            tag=base64.b64decode(data["tag"]),
            ksr_sha256=str(data["ksr_sha256"]),
        )

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_string())

    def to_string(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def read(cls, path: str | Path) -> "KsrEnvelope":
        return cls.from_dict(json.loads(Path(path).read_text()))


def derive_key(
    password: str,
    salt: bytes,
    whitepaper_hash: str | None = None,
    iterations: int | None = None,
) -> bytes:
    """Derive a 256-bit AES key from a password bound to the whitepaper hash.

    Binding the password to the whitepaper hash means the key material is
    intrinsically tied to the reference document. The whitepaper hash is part
    of the public `semantic_registry.yaml`, so it does not need to be secret;
    it only ensures that a key derived for one reference document cannot
    decrypt a KSR built for a different reference document.
    """
    if iterations is None:
        iterations = int(os.getenv("DSS_KSR_PBKDF2_ITERATIONS", "480000"))
    passphrase = password
    if whitepaper_hash:
        passphrase = f"{password}:{whitepaper_hash}"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
        backend=default_backend(),
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_ksr(
    plaintext: bytes,
    password: str,
    whitepaper_hash: str | None = None,
    iterations: int | None = None,
) -> KsrEnvelope:
    """Encrypt the KSR plaintext and return a content-addressed envelope."""
    salt = os.urandom(32)
    key = derive_key(password, salt, whitepaper_hash, iterations)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)
    ciphertext = ciphertext_with_tag[:-16]
    tag = ciphertext_with_tag[-16:]
    ksr_sha256 = hashlib.sha256(plaintext).hexdigest()
    return KsrEnvelope(
        version=1,
        salt=salt,
        nonce=nonce,
        ciphertext=ciphertext,
        tag=tag,
        ksr_sha256=ksr_sha256,
    )


def decrypt_ksr(
    envelope: KsrEnvelope,
    password: str,
    whitepaper_hash: str | None = None,
    iterations: int | None = None,
    expected_sha256: str | None = None,
) -> bytes:
    """Decrypt and authenticate the KSR envelope.

    Raises:
        KsrAuthenticationError: if the GCM tag does not verify.
        KsrIntegrityError: if `expected_sha256` is supplied and the plaintext
            hash does not match.
    """
    key = derive_key(password, envelope.salt, whitepaper_hash, iterations)
    aesgcm = AESGCM(key)
    ciphertext_with_tag = envelope.ciphertext + envelope.tag
    try:
        plaintext = aesgcm.decrypt(envelope.nonce, ciphertext_with_tag, None)
    except Exception as exc:  # cryptography raises InvalidTag or InvalidKey
        raise KsrAuthenticationError("KSR envelope authentication failed") from exc

    if expected_sha256 is not None:
        actual = hashlib.sha256(plaintext).hexdigest()
        if actual != expected_sha256:
            raise KsrIntegrityError(
                f"KSR content hash mismatch: expected {expected_sha256}, got {actual}"
            )

    return plaintext


def load_ksr_yaml(password: str, envelope_path: str | Path | None = None) -> dict[str, Any]:
    """Decrypt the KSR envelope and load the YAML payload.

    This is intended for build-time and test-time use only.
    """
    if envelope_path is None:
        envelope_path = Path(__file__).with_name("semantic_registry.enc")
    else:
        envelope_path = Path(envelope_path)

    envelope = KsrEnvelope.read(envelope_path)
    ksr_dir = Path(__file__).parent / ".ksr"
    expected = (ksr_dir / "ksr.hash").read_text().strip()
    whitepaper_hash = (ksr_dir / "whitepaper.hash").read_text().strip()
    plaintext = decrypt_ksr(
        envelope, password, whitepaper_hash=whitepaper_hash, expected_sha256=expected
    )
    return yaml.safe_load(plaintext)
