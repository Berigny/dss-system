"""Tests for the middleware E6 packet helper compatibility layer."""

import pytest

from utils.e6_packet import (
    CHECKSUM_336,
    HEADER_SIZE_BYTES,
    HEADER_SIZE_BYTES_V1,
    MAGIC_V0,
    VERSION_V0,
    VERSION_V1,
    compute_checksum_336_field,
    pack_header_v0,
    pack_header_v1,
    pack_patch_status,
    unpack_header_v0,
    unpack_header_v1,
    unpack_patch_status,
)


def _sample_kwargs() -> dict:
    return {
        "mode": 1,
        "ptype": 2,
        "law": 3,
        "route": 0,
        "node": 5,
        "K": 1,
        "P": 0,
        "E": 1,
        "valid": 1,
        "dW": -3,
        "seq": 0x123456,
        "t_ms": 0xABCDEF,
        "V_q": 0xBEEF,
    }


def test_pack_unpack_v0_roundtrip() -> None:
    header = pack_header_v0(**_sample_kwargs())
    assert len(header) == HEADER_SIZE_BYTES
    decoded = unpack_header_v0(header)
    assert decoded["magic"] == MAGIC_V0
    assert decoded["ver"] == VERSION_V0
    assert decoded["crc_ok"] is True


def test_v1_header_carries_patch_bits_and_checksum() -> None:
    status = {f"patch_{i:03d}": True for i in range(1, 11)}
    status_int = pack_patch_status(status)
    header = pack_header_v1(**_sample_kwargs(), patch_status=status_int)
    assert len(header) == HEADER_SIZE_BYTES_V1
    decoded = unpack_header_v1(header)
    assert decoded["ver"] == VERSION_V1
    assert decoded["patch_status_int"] == status_int
    assert decoded["patch_status"] == status
    assert decoded["checksum_336"] == CHECKSUM_336
    assert decoded["checksum_336_pass"] is True


def test_v1_checksum_defaults_to_zero_when_patches_fail() -> None:
    header = pack_header_v1(**_sample_kwargs(), patch_status=0)
    decoded = unpack_header_v1(header)
    assert decoded["checksum_336"] == 0
    assert decoded["checksum_336_pass"] is False


def test_compute_checksum_336_field() -> None:
    assert compute_checksum_336_field(0x3FF) == CHECKSUM_336
    assert compute_checksum_336_field(0x3FE) == 0


def test_unpack_patch_status_roundtrip() -> None:
    status = {f"patch_{i:03d}": (i % 2 == 0) for i in range(1, 11)}
    value = pack_patch_status(status)
    assert unpack_patch_status(value) == status


def test_v1_prefix_is_valid_v0_header() -> None:
    header_v1 = pack_header_v1(**_sample_kwargs(), patch_status=0x3FF)
    decoded_v0 = unpack_header_v0(header_v1[:HEADER_SIZE_BYTES])
    assert decoded_v0["crc_ok"] is True
    assert decoded_v0["ver"] == VERSION_V1
