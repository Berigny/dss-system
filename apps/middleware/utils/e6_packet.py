"""Self-contained E6 header/envelope helpers (v0/v1).

This module mirrors the canonical backend codec in
`ds-backend-local/backend/fieldx_kernel/e6_packet.py` and
`ds-backend-local/backend/fieldx_kernel/e6_envelope.py` without importing the
backend package, so it can be used by middleware scripts that must remain
lightweight and runnable from arbitrary working directories.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

MAGIC_V0 = 0x4347
VERSION_V0 = 1
FORMAT_V0 = 0
HEADER_SIZE_BYTES = 16

VERSION_V1 = 2
FORMAT_V1 = 0
HEADER_SIZE_BYTES_V1 = 20

PATCH_IDS = (
    "patch_001",
    "patch_002",
    "patch_003",
    "patch_004",
    "patch_005",
    "patch_006",
    "patch_007",
    "patch_008",
    "patch_009",
    "patch_010",
)
PATCH_STATUS_MASK = 0x3FF
CHECKSUM_336 = 336


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return lo if value < lo else hi if value > hi else value


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly=0x1021 init=0xFFFF xorout=0x0000."""
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte & 0xFF) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def hash64(data: bytes) -> int:
    """Compact 64-bit hash for envelope ids and payload commitments."""
    return int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big")


def pack_header_v0(
    *,
    mode: int,
    ptype: int,
    law: int,
    route: int,
    node: int,
    K: int,
    P: int,
    E: int,
    valid: int,
    dW: int,
    seq: int,
    t_ms: int,
    V_q: int,
    ver: int = VERSION_V0,
    fmt: int = FORMAT_V0,
) -> bytes:
    """Pack E6 header v0 to 16 bytes (big-endian)."""
    mode = _clamp_int(int(mode), 0, 3)
    ptype = _clamp_int(int(ptype), 0, 3)
    law = _clamp_int(int(law), 0, 3)
    route = _clamp_int(int(route), 0, 3)
    node = _clamp_int(int(node), 0, 15)
    dW = _clamp_int(int(dW), -128, 127)
    seq = int(seq) & 0xFFFFFF
    t_ms = int(t_ms) & 0xFFFFFF
    V_q = _clamp_int(int(V_q), 0, 0xFFFF)
    ver = _clamp_int(int(ver), 0, 15)
    fmt = _clamp_int(int(fmt), 0, 15)

    flags = (
        ((int(K) & 0x1) << 0)
        | ((int(P) & 0x1) << 1)
        | ((int(E) & 0x1) << 2)
        | ((int(valid) & 0x1) << 3)
    )

    raw = bytearray(HEADER_SIZE_BYTES)
    raw[0:2] = MAGIC_V0.to_bytes(2, "big")
    raw[2] = ((ver & 0xF) << 4) | (fmt & 0xF)
    raw[3] = (
        ((mode & 0x3) << 6)
        | ((ptype & 0x3) << 4)
        | ((law & 0x3) << 2)
        | (route & 0x3)
    )
    raw[4] = ((node & 0xF) << 4) | (flags & 0xF)
    raw[5] = dW & 0xFF
    raw[6:9] = seq.to_bytes(3, "big")
    raw[9:12] = t_ms.to_bytes(3, "big")
    raw[12:14] = V_q.to_bytes(2, "big")
    raw[14:16] = crc16_ccitt_false(bytes(raw[0:14])).to_bytes(2, "big")
    return bytes(raw)


def unpack_header_v0(data: bytes) -> dict[str, Any]:
    """Unpack E6 header v0 from 16 bytes and validate CRC."""
    if not isinstance(data, (bytes, bytearray)) or len(data) != HEADER_SIZE_BYTES:
        raise ValueError("header must be exactly 16 bytes")

    buf = bytes(data)
    magic = int.from_bytes(buf[0:2], "big")
    ver = (buf[2] >> 4) & 0xF
    fmt = buf[2] & 0xF
    b3 = buf[3]
    mode = (b3 >> 6) & 0x3
    ptype = (b3 >> 4) & 0x3
    law = (b3 >> 2) & 0x3
    route = b3 & 0x3
    b4 = buf[4]
    node = (b4 >> 4) & 0xF
    flags = b4 & 0xF
    dW_u8 = buf[5]
    dW = dW_u8 - 256 if dW_u8 >= 128 else dW_u8
    seq = int.from_bytes(buf[6:9], "big")
    t_ms = int.from_bytes(buf[9:12], "big")
    V_q = int.from_bytes(buf[12:14], "big")
    crc16 = int.from_bytes(buf[14:16], "big")
    expected_crc16 = crc16_ccitt_false(buf[0:14])

    return {
        "magic": magic,
        "ver": ver,
        "fmt": fmt,
        "mode": mode,
        "ptype": ptype,
        "law": law,
        "route": route,
        "node": node,
        "flags": flags,
        "K": flags & 0x1,
        "P": (flags >> 1) & 0x1,
        "E": (flags >> 2) & 0x1,
        "valid": (flags >> 3) & 0x1,
        "dW": dW,
        "seq": seq,
        "t_ms": t_ms,
        "V_q": V_q,
        "crc16": crc16,
        "crc16_expected": expected_crc16,
        "crc_ok": crc16 == expected_crc16,
    }


def pack_patch_status(status_map: Mapping[str, bool]) -> int:
    """Pack a {patch_id: passed} map into a 10-bit integer."""
    value = 0
    for idx, patch_id in enumerate(PATCH_IDS):
        if status_map.get(patch_id):
            value |= 1 << idx
    return value & PATCH_STATUS_MASK


def unpack_patch_status(value: int) -> dict[str, bool]:
    """Unpack a 10-bit integer into a {patch_id: passed} map."""
    value = int(value) & PATCH_STATUS_MASK
    return {
        patch_id: bool(value & (1 << idx))
        for idx, patch_id in enumerate(PATCH_IDS)
    }


def compute_checksum_336_field(patch_status: int) -> int:
    """Return CHECKSUM_336 iff all patch bits are set, else 0."""
    return CHECKSUM_336 if (int(patch_status) & PATCH_STATUS_MASK) == PATCH_STATUS_MASK else 0


def pack_header_v1(
    *,
    mode: int,
    ptype: int,
    law: int,
    route: int,
    node: int,
    K: int,
    P: int,
    E: int,
    valid: int,
    dW: int,
    seq: int,
    t_ms: int,
    V_q: int,
    patch_status: int = 0,
    checksum_336: int | None = None,
    ver: int = VERSION_V1,
    fmt: int = FORMAT_V1,
) -> bytes:
    """Pack E6 header v1 to 20 bytes (big-endian).

    The first 16 bytes are the v0-compatible prefix. Bytes 16-19 carry:
      - bits 0-9:   patch status (one bit per patch 001..010)
      - bits 10-25: 336 checksum field (0 or 336)
      - bits 26-31: reserved
    """
    base = pack_header_v0(
        mode=mode,
        ptype=ptype,
        law=law,
        route=route,
        node=node,
        K=K,
        P=P,
        E=E,
        valid=valid,
        dW=dW,
        seq=seq,
        t_ms=t_ms,
        V_q=V_q,
        ver=ver,
        fmt=fmt,
    )
    if checksum_336 is None:
        checksum_336 = compute_checksum_336_field(patch_status)
    ext = (
        (int(patch_status) & PATCH_STATUS_MASK)
        | ((int(checksum_336) & 0xFFFF) << 10)
    )
    return base + ext.to_bytes(4, "big")


def unpack_header_v1(data: bytes) -> dict[str, Any]:
    """Unpack E6 header v1 from 20 bytes and validate the v0 CRC."""
    if not isinstance(data, (bytes, bytearray)) or len(data) != HEADER_SIZE_BYTES_V1:
        raise ValueError(f"v1 header must be exactly {HEADER_SIZE_BYTES_V1} bytes")

    base = unpack_header_v0(data[:HEADER_SIZE_BYTES])
    ext = int.from_bytes(data[HEADER_SIZE_BYTES:HEADER_SIZE_BYTES_V1], "big")
    patch_status_int = ext & PATCH_STATUS_MASK
    checksum_336 = (ext >> 10) & 0xFFFF

    result = dict(base)
    result["patch_status_int"] = patch_status_int
    result["patch_status"] = unpack_patch_status(patch_status_int)
    result["checksum_336"] = checksum_336
    result["checksum_336_pass"] = checksum_336 == CHECKSUM_336
    return result


def build_signing_input(header: bytes, trailer: dict[str, int]) -> bytes:
    """Build the canonical signed octets for a v0/v1 envelope."""
    if len(header) not in (HEADER_SIZE_BYTES, HEADER_SIZE_BYTES_V1):
        raise ValueError("header must be 16 or 20 bytes")

    def u32(value: int) -> bytes:
        return int(value).to_bytes(4, "big", signed=False)

    def u64(value: int) -> bytes:
        return int(value).to_bytes(8, "big", signed=False)

    return b"".join(
        [
            header,
            u64(trailer["payload_hash_h64"]),
            u64(trailer["ledger_id_h64"]),
            u64(trailer["origin_repo_h64"]),
            u64(trailer["origin_node_h64"]),
            u64(trailer["subject_id_h64"]),
            u64(trailer["issuer_id_h64"]),
            u64(trailer["nonce64"]),
            u64(trailer["prev_event_h64"]),
            u32(trailer["key_id"]),
            bytes([trailer["alg_id"] & 0xFF]),
        ]
    )


def pack_envelope_v0(header: bytes, payload: bytes, trailer: dict[str, int], proof: bytes) -> bytes:
    """Pack a v0/v1 envelope frame using a dict trailer."""
    trailer_blob = b"".join(
        [
            bytes([0]),
            bytes([trailer["alg_id"] & 0xFF]),
            int(trailer["key_id"]).to_bytes(4, "big"),
            int(trailer["ledger_id_h64"]).to_bytes(8, "big"),
            int(trailer["origin_repo_h64"]).to_bytes(8, "big"),
            int(trailer["origin_node_h64"]).to_bytes(8, "big"),
            int(trailer["subject_id_h64"]).to_bytes(8, "big"),
            int(trailer["issuer_id_h64"]).to_bytes(8, "big"),
            int(trailer["nonce64"]).to_bytes(8, "big"),
            int(trailer["prev_event_h64"]).to_bytes(8, "big"),
            int(trailer["payload_hash_h64"]).to_bytes(8, "big"),
            bytes([len(proof)]),
            proof,
        ]
    )
    return b"".join([header, len(payload).to_bytes(2, "big"), payload, trailer_blob])
