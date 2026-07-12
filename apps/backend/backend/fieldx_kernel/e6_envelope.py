"""E6 envelope v0 codec and verifier utilities."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Callable

from backend.fieldx_kernel.e6_packet import MAGIC_V0, unpack_header_v0

try:  # Optional dependency for native Ed25519 verification.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except Exception:  # pragma: no cover - dependency is optional at runtime
    InvalidSignature = Exception
    Ed25519PrivateKey = None
    Ed25519PublicKey = None

ALG_ED25519 = 1
ALG_HMAC_SHA256 = 2
TRAILER_V0 = 0
HEADER_SIZE = 16
PAYLOAD_LEN_SIZE = 2
TRAILER_FIXED_SIZE = 71
EVENT_ID_SIZE = 8


def _u32(value: int) -> bytes:
    return int(value).to_bytes(4, "big", signed=False)


def _u64(value: int) -> bytes:
    return int(value).to_bytes(8, "big", signed=False)


def _u24(value: int) -> bytes:
    return int(value).to_bytes(3, "big", signed=False)


def hash64(data: bytes) -> int:
    """Compact 64-bit hash for envelope ids and payload commitments."""
    return int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big")


@dataclass(frozen=True)
class TrailerV0:
    trailer_ver: int
    alg_id: int
    key_id: int
    ledger_id_h64: int
    origin_repo_h64: int
    origin_node_h64: int
    subject_id_h64: int
    issuer_id_h64: int
    nonce64: int
    prev_event_h64: int
    payload_hash_h64: int
    proof: bytes


def pack_trailer_v0(trailer: TrailerV0) -> bytes:
    proof = trailer.proof or b""
    if len(proof) > 255:
        raise ValueError("proof too long for trailer v0")
    return b"".join(
        [
            bytes([int(trailer.trailer_ver) & 0xFF]),
            bytes([int(trailer.alg_id) & 0xFF]),
            _u32(trailer.key_id & 0xFFFFFFFF),
            _u64(trailer.ledger_id_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.origin_repo_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.origin_node_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.subject_id_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.issuer_id_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.nonce64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.prev_event_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.payload_hash_h64 & 0xFFFFFFFFFFFFFFFF),
            bytes([len(proof)]),
            proof,
        ]
    )


def unpack_trailer_v0(data: bytes, offset: int = 0) -> tuple[TrailerV0, int]:
    if len(data) < offset + TRAILER_FIXED_SIZE:
        raise ValueError("trailer too short")
    pos = offset
    trailer_ver = data[pos]
    pos += 1
    alg_id = data[pos]
    pos += 1
    key_id = int.from_bytes(data[pos : pos + 4], "big")
    pos += 4
    ledger_id_h64 = int.from_bytes(data[pos : pos + 8], "big")
    pos += 8
    origin_repo_h64 = int.from_bytes(data[pos : pos + 8], "big")
    pos += 8
    origin_node_h64 = int.from_bytes(data[pos : pos + 8], "big")
    pos += 8
    subject_id_h64 = int.from_bytes(data[pos : pos + 8], "big")
    pos += 8
    issuer_id_h64 = int.from_bytes(data[pos : pos + 8], "big")
    pos += 8
    nonce64 = int.from_bytes(data[pos : pos + 8], "big")
    pos += 8
    prev_event_h64 = int.from_bytes(data[pos : pos + 8], "big")
    pos += 8
    payload_hash_h64 = int.from_bytes(data[pos : pos + 8], "big")
    pos += 8
    proof_len = data[pos]
    pos += 1
    if len(data) < pos + proof_len:
        raise ValueError("trailer proof truncated")
    proof = data[pos : pos + proof_len]
    pos += proof_len
    return (
        TrailerV0(
            trailer_ver=trailer_ver,
            alg_id=alg_id,
            key_id=key_id,
            ledger_id_h64=ledger_id_h64,
            origin_repo_h64=origin_repo_h64,
            origin_node_h64=origin_node_h64,
            subject_id_h64=subject_id_h64,
            issuer_id_h64=issuer_id_h64,
            nonce64=nonce64,
            prev_event_h64=prev_event_h64,
            payload_hash_h64=payload_hash_h64,
            proof=proof,
        ),
        pos,
    )


def build_signing_input(header: bytes, trailer: TrailerV0) -> bytes:
    if len(header) != HEADER_SIZE:
        raise ValueError("header must be 16 bytes")
    return b"".join(
        [
            header,
            _u64(trailer.payload_hash_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.ledger_id_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.origin_repo_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.origin_node_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.subject_id_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.issuer_id_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.nonce64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.prev_event_h64 & 0xFFFFFFFFFFFFFFFF),
            _u32(trailer.key_id & 0xFFFFFFFF),
            bytes([trailer.alg_id & 0xFF]),
        ]
    )


def pack_envelope_v0(*, header: bytes, payload: bytes, trailer: TrailerV0) -> bytes:
    if len(header) != HEADER_SIZE:
        raise ValueError("header must be 16 bytes")
    if len(payload) > 0xFFFF:
        raise ValueError("payload too large for envelope v0 frame")
    trailer_blob = pack_trailer_v0(trailer)
    return b"".join([header, len(payload).to_bytes(2, "big"), payload, trailer_blob])


def sign_trailer_v0_hmac(
    *,
    header: bytes,
    trailer: TrailerV0,
    key: bytes,
    proof_size: int = 16,
) -> TrailerV0:
    """Return a trailer with HMAC proof applied."""
    proof_size = max(1, min(int(proof_size), 32))
    unsigned = TrailerV0(**{**trailer.__dict__, "proof": b""})
    signing_input = build_signing_input(header, unsigned)
    proof = hmac.new(key, signing_input, hashlib.sha256).digest()[:proof_size]
    return TrailerV0(**{**unsigned.__dict__, "proof": proof})


def sign_trailer_v0_ed25519(
    *,
    header: bytes,
    trailer: TrailerV0,
    private_key_bytes: bytes,
) -> TrailerV0:
    """Return a trailer with Ed25519 signature proof applied."""
    if Ed25519PrivateKey is None:
        raise RuntimeError("cryptography is required for Ed25519 signing")
    unsigned = TrailerV0(**{**trailer.__dict__, "proof": b""})
    signing_input = build_signing_input(header, unsigned)
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    proof = private_key.sign(signing_input)
    return TrailerV0(**{**unsigned.__dict__, "proof": proof})


def unpack_envelope_v0(data: bytes) -> dict[str, Any]:
    if len(data) < HEADER_SIZE + PAYLOAD_LEN_SIZE + TRAILER_FIXED_SIZE:
        raise ValueError("envelope too short")
    header = data[:HEADER_SIZE]
    payload_len = int.from_bytes(data[HEADER_SIZE : HEADER_SIZE + PAYLOAD_LEN_SIZE], "big")
    payload_start = HEADER_SIZE + PAYLOAD_LEN_SIZE
    payload_end = payload_start + payload_len
    if len(data) < payload_end + TRAILER_FIXED_SIZE:
        raise ValueError("payload/trailer truncated")
    payload = data[payload_start:payload_end]
    trailer, final_pos = unpack_trailer_v0(data, payload_end)
    if final_pos != len(data):
        raise ValueError("unexpected trailing bytes in envelope")
    return {
        "header": header,
        "payload": payload,
        "trailer": trailer,
    }


def compute_event_id_h64(*, trailer: TrailerV0, seq: int) -> int:
    base = b"".join(
        [
            _u64(trailer.ledger_id_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.origin_repo_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.origin_node_h64 & 0xFFFFFFFFFFFFFFFF),
            _u24(int(seq) & 0xFFFFFF),
            _u64(trailer.prev_event_h64 & 0xFFFFFFFFFFFFFFFF),
            _u64(trailer.payload_hash_h64 & 0xFFFFFFFFFFFFFFFF),
        ]
    )
    return hash64(base)


def stream_key_from_trailer(trailer: TrailerV0) -> str:
    return (
        f"{trailer.ledger_id_h64:016x}:"
        f"{trailer.origin_repo_h64:016x}:"
        f"{trailer.origin_node_h64:016x}"
    )


def verify_envelope_v0(
    envelope: bytes,
    *,
    hmac_key_resolver: Callable[[int], bytes | None] | None = None,
    ed25519_public_key_resolver: Callable[[int], bytes | None] | None = None,
    ed25519_verifier: Callable[[int, bytes, bytes], bool] | None = None,
) -> dict[str, Any]:
    parsed = unpack_envelope_v0(envelope)
    header = parsed["header"]
    payload = parsed["payload"]
    trailer: TrailerV0 = parsed["trailer"]

    if trailer.trailer_ver != TRAILER_V0:
        return {"ok": False, "reason": "unsupported_trailer_ver"}

    header_fields = unpack_header_v0(header)
    if header_fields.get("magic") != MAGIC_V0:
        return {"ok": False, "reason": "bad_magic"}
    if not bool(header_fields.get("crc_ok")):
        return {"ok": False, "reason": "bad_crc"}

    payload_hash = hash64(payload)
    if payload_hash != trailer.payload_hash_h64:
        return {"ok": False, "reason": "payload_hash_mismatch"}

    signing_input = build_signing_input(header, trailer)
    if trailer.alg_id == ALG_HMAC_SHA256:
        if hmac_key_resolver is None:
            return {"ok": False, "reason": "missing_key_resolver"}
        key = hmac_key_resolver(trailer.key_id)
        if not key:
            return {"ok": False, "reason": "unknown_key_id"}
        expected = hmac.new(key, signing_input, hashlib.sha256).digest()[: len(trailer.proof)]
        if len(trailer.proof) == 0 or not hmac.compare_digest(expected, trailer.proof):
            return {"ok": False, "reason": "bad_proof"}
    elif trailer.alg_id == ALG_ED25519:
        if ed25519_verifier is not None:
            try:
                verified = bool(ed25519_verifier(trailer.key_id, signing_input, trailer.proof))
            except Exception:
                verified = False
            if not verified:
                return {"ok": False, "reason": "bad_proof"}
        elif Ed25519PublicKey is not None and ed25519_public_key_resolver is not None:
            public_key_bytes = ed25519_public_key_resolver(trailer.key_id)
            if not public_key_bytes:
                return {"ok": False, "reason": "unknown_key_id"}
            try:
                public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
                public_key.verify(trailer.proof, signing_input)
            except InvalidSignature:
                return {"ok": False, "reason": "bad_proof"}
            except Exception:
                return {"ok": False, "reason": "invalid_public_key"}
        else:
            return {"ok": False, "reason": "missing_ed25519_verifier"}
    else:
        return {"ok": False, "reason": "unknown_alg"}

    seq = int(header_fields.get("seq", 0)) & 0xFFFFFF
    event_id_h64 = compute_event_id_h64(trailer=trailer, seq=seq)
    return {
        "ok": True,
        "header": header_fields,
        "payload": payload,
        "trailer": trailer,
        "event_id_h64": event_id_h64,
        "event_id": f"{event_id_h64:016x}",
        "stream_key": stream_key_from_trailer(trailer),
    }


__all__ = [
    "ALG_ED25519",
    "ALG_HMAC_SHA256",
    "TRAILER_V0",
    "TrailerV0",
    "build_signing_input",
    "compute_event_id_h64",
    "hash64",
    "pack_envelope_v0",
    "pack_trailer_v0",
    "sign_trailer_v0_ed25519",
    "sign_trailer_v0_hmac",
    "stream_key_from_trailer",
    "unpack_envelope_v0",
    "unpack_trailer_v0",
    "verify_envelope_v0",
]
