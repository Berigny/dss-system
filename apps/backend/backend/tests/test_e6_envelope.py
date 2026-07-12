from __future__ import annotations

import hashlib
import hmac

import pytest

from backend.fieldx_kernel.e6_envelope import (
    ALG_ED25519,
    ALG_HMAC_SHA256,
    TRAILER_V0,
    TrailerV0,
    build_signing_input,
    pack_envelope_v0,
    sign_trailer_v0_ed25519,
    unpack_envelope_v0,
    verify_envelope_v0,
    hash64,
)
from backend.fieldx_kernel.e6_packet import pack_header_v0


def test_envelope_v0_roundtrip_and_verify_hmac() -> None:
    header = pack_header_v0(
        mode=2,
        ptype=0,
        law=2,
        route=3,
        node=4,
        K=1,
        P=1,
        E=1,
        valid=1,
        dW=0,
        seq=1,
        t_ms=100,
        V_q=50000,
    )
    payload = b'{"kind":"WU","msg":"ok"}'
    trailer = TrailerV0(
        trailer_ver=TRAILER_V0,
        alg_id=ALG_HMAC_SHA256,
        key_id=1,
        ledger_id_h64=0x1111,
        origin_repo_h64=0x2222,
        origin_node_h64=0x3333,
        subject_id_h64=0x4444,
        issuer_id_h64=0x5555,
        nonce64=0x6666,
        prev_event_h64=0,
        payload_hash_h64=hash64(payload),
        proof=b"",
    )
    signing_input = build_signing_input(header, trailer)
    proof = hmac.new(b"unit-test-secret", signing_input, hashlib.sha256).digest()[:16]
    trailer = TrailerV0(**{**trailer.__dict__, "proof": proof})
    envelope = pack_envelope_v0(header=header, payload=payload, trailer=trailer)

    unpacked = unpack_envelope_v0(envelope)
    assert unpacked["header"] == header
    assert unpacked["payload"] == payload
    assert unpacked["trailer"].proof == proof

    verified = verify_envelope_v0(
        envelope,
        hmac_key_resolver=lambda key_id: b"unit-test-secret" if key_id == 1 else None,
    )
    assert verified["ok"] is True
    assert verified["event_id"]
    assert verified["stream_key"] == "0000000000001111:0000000000002222:0000000000003333"


def test_envelope_v0_rejects_bad_proof() -> None:
    header = pack_header_v0(
        mode=2,
        ptype=0,
        law=2,
        route=3,
        node=4,
        K=1,
        P=1,
        E=1,
        valid=1,
        dW=0,
        seq=2,
        t_ms=101,
        V_q=50000,
    )
    payload = b'{"kind":"WU","msg":"tampered"}'
    trailer = TrailerV0(
        trailer_ver=TRAILER_V0,
        alg_id=ALG_HMAC_SHA256,
        key_id=1,
        ledger_id_h64=0x1111,
        origin_repo_h64=0x2222,
        origin_node_h64=0x3333,
        subject_id_h64=0x4444,
        issuer_id_h64=0x5555,
        nonce64=0x7777,
        prev_event_h64=0,
        payload_hash_h64=hash64(payload),
        proof=b"\x00" * 16,
    )
    envelope = pack_envelope_v0(header=header, payload=payload, trailer=trailer)
    verified = verify_envelope_v0(
        envelope,
        hmac_key_resolver=lambda key_id: b"unit-test-secret" if key_id == 1 else None,
    )
    assert verified["ok"] is False
    assert verified["reason"] == "bad_proof"


def test_envelope_v0_ed25519_uses_injected_verifier() -> None:
    header = pack_header_v0(
        mode=2,
        ptype=0,
        law=2,
        route=3,
        node=4,
        K=1,
        P=1,
        E=1,
        valid=1,
        dW=0,
        seq=3,
        t_ms=102,
        V_q=50000,
    )
    payload = b'{"kind":"WU","msg":"ed25519"}'
    trailer = TrailerV0(
        trailer_ver=TRAILER_V0,
        alg_id=ALG_ED25519,
        key_id=42,
        ledger_id_h64=0x1111,
        origin_repo_h64=0x2222,
        origin_node_h64=0x3333,
        subject_id_h64=0x4444,
        issuer_id_h64=0x5555,
        nonce64=0x8888,
        prev_event_h64=0,
        payload_hash_h64=hash64(payload),
        proof=b"ed-proof",
    )
    envelope = pack_envelope_v0(header=header, payload=payload, trailer=trailer)

    ok = verify_envelope_v0(
        envelope,
        ed25519_verifier=lambda key_id, signing_input, proof: (
            key_id == 42 and proof == b"ed-proof" and len(signing_input) > 16
        ),
    )
    assert ok["ok"] is True

    bad = verify_envelope_v0(
        envelope,
        ed25519_verifier=lambda key_id, signing_input, proof: False,
    )
    assert bad["ok"] is False
    assert bad["reason"] == "bad_proof"


def test_envelope_v0_ed25519_sign_and_verify_with_public_key_resolver() -> None:
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    private_key = crypto.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_raw = private_key.private_bytes_raw()
    public_raw = public_key.public_bytes_raw()

    header = pack_header_v0(
        mode=2,
        ptype=0,
        law=2,
        route=3,
        node=4,
        K=1,
        P=1,
        E=1,
        valid=1,
        dW=0,
        seq=4,
        t_ms=103,
        V_q=50000,
    )
    payload = b'{"kind":"WU","msg":"ed25519-native"}'
    trailer = TrailerV0(
        trailer_ver=TRAILER_V0,
        alg_id=ALG_ED25519,
        key_id=99,
        ledger_id_h64=0x1111,
        origin_repo_h64=0x2222,
        origin_node_h64=0x3333,
        subject_id_h64=0x4444,
        issuer_id_h64=0x5555,
        nonce64=0x9999,
        prev_event_h64=0,
        payload_hash_h64=hash64(payload),
        proof=b"",
    )
    signed = sign_trailer_v0_ed25519(
        header=header,
        trailer=trailer,
        private_key_bytes=private_raw,
    )
    envelope = pack_envelope_v0(header=header, payload=payload, trailer=signed)
    verified = verify_envelope_v0(
        envelope,
        ed25519_public_key_resolver=lambda key_id: public_raw if key_id == 99 else None,
    )
    assert verified["ok"] is True
