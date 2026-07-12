"""Compact E6 128-bit header codec (v0).

Canonical layout follows backend/utils/ref/128-bit-field-map.txt section
"128-bit header v0" (magic 0x4347).
"""

from __future__ import annotations

from typing import Any, Dict

MAGIC_V0 = 0x4347
VERSION_V0 = 1
FORMAT_V0 = 0
HEADER_SIZE_BYTES = 16


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
    raw[0:2] = MAGIC_V0.to_bytes(2, 'big')
    raw[2] = ((ver & 0xF) << 4) | (fmt & 0xF)
    raw[3] = ((mode & 0x3) << 6) | ((ptype & 0x3) << 4) | ((law & 0x3) << 2) | (route & 0x3)
    raw[4] = ((node & 0xF) << 4) | (flags & 0xF)
    raw[5] = dW & 0xFF
    raw[6:9] = seq.to_bytes(3, 'big')
    raw[9:12] = t_ms.to_bytes(3, 'big')
    raw[12:14] = V_q.to_bytes(2, 'big')

    crc = crc16_ccitt_false(bytes(raw[0:14]))
    raw[14:16] = crc.to_bytes(2, 'big')
    return bytes(raw)


def unpack_header_v0(data: bytes) -> Dict[str, Any]:
    """Unpack E6 header v0 from 16 bytes and validate CRC."""
    if not isinstance(data, (bytes, bytearray)) or len(data) != HEADER_SIZE_BYTES:
        raise ValueError('header must be exactly 16 bytes')

    buf = bytes(data)
    magic = int.from_bytes(buf[0:2], 'big')
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
    seq = int.from_bytes(buf[6:9], 'big')
    t_ms = int.from_bytes(buf[9:12], 'big')
    V_q = int.from_bytes(buf[12:14], 'big')
    crc16 = int.from_bytes(buf[14:16], 'big')
    expected_crc16 = crc16_ccitt_false(buf[0:14])

    return {
        'magic': magic,
        'ver': ver,
        'fmt': fmt,
        'mode': mode,
        'ptype': ptype,
        'law': law,
        'route': route,
        'node': node,
        'flags': flags,
        'K': flags & 0x1,
        'P': (flags >> 1) & 0x1,
        'E': (flags >> 2) & 0x1,
        'valid': (flags >> 3) & 0x1,
        'dW': dW,
        'seq': seq,
        't_ms': t_ms,
        'V_q': V_q,
        'crc16': crc16,
        'crc16_expected': expected_crc16,
        'crc_ok': crc16 == expected_crc16,
    }


__all__ = [
    'MAGIC_V0',
    'VERSION_V0',
    'FORMAT_V0',
    'HEADER_SIZE_BYTES',
    'crc16_ccitt_false',
    'pack_header_v0',
    'unpack_header_v0',
]
