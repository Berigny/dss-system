#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import sys
from dataclasses import dataclass
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MAGIC_V0 = 0x4347
VERSION_V0 = 1
FORMAT_V0 = 0
ALG_ED25519 = 1
TRAILER_V0 = 0


@dataclass
class CheckResult:
    name: str
    ok: bool
    details: str


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return lo if value < lo else hi if value > hi else value


def crc16_ccitt_false(data: bytes) -> int:
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
    return int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big")


def pack_header_v0(*, seq: int, t_ms: int = 0, node: int = 4, v_q: int = 45000) -> bytes:
    mode = 2
    ptype = 0
    law = 2
    route = 3
    d_w = 0
    flags = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)  # K/P/E/valid

    seq = int(seq) & 0xFFFFFF
    t_ms = int(t_ms) & 0xFFFFFF
    node = _clamp_int(int(node), 0, 15)
    d_w = _clamp_int(int(d_w), -128, 127)
    v_q = _clamp_int(int(v_q), 0, 0xFFFF)

    raw = bytearray(16)
    raw[0:2] = MAGIC_V0.to_bytes(2, "big")
    raw[2] = ((VERSION_V0 & 0xF) << 4) | (FORMAT_V0 & 0xF)
    raw[3] = ((mode & 0x3) << 6) | ((ptype & 0x3) << 4) | ((law & 0x3) << 2) | (route & 0x3)
    raw[4] = ((node & 0xF) << 4) | (flags & 0xF)
    raw[5] = d_w & 0xFF
    raw[6:9] = seq.to_bytes(3, "big")
    raw[9:12] = t_ms.to_bytes(3, "big")
    raw[12:14] = v_q.to_bytes(2, "big")
    raw[14:16] = crc16_ccitt_false(bytes(raw[0:14])).to_bytes(2, "big")
    return bytes(raw)


def build_signing_input(header: bytes, trailer: dict[str, int]) -> bytes:
    def u32(v: int) -> bytes:
        return int(v).to_bytes(4, "big", signed=False)

    def u64(v: int) -> bytes:
        return int(v).to_bytes(8, "big", signed=False)

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


def pack_envelope(header: bytes, payload: bytes, trailer: dict[str, int], proof: bytes) -> bytes:
    blob = b"".join(
        [
            bytes([TRAILER_V0]),
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
    return b"".join([header, len(payload).to_bytes(2, "big"), payload, blob])


def _post_json(url: str, payload: dict) -> dict:
    req = Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_signed_envelope(
    *,
    private_key_hex: str,
    key_id: int,
    seq: int,
    prev_event_h64: int,
    ledger_id: str,
    origin_repo: str,
    origin_node: str,
    subject: str,
    issuer: str,
    nonce64: int,
    message: str,
) -> bytes:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))

    payload = json.dumps(
        {"kind": "WU", "msg": message, "seq": seq},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    header = pack_header_v0(seq=seq, t_ms=seq)
    trailer = {
        "alg_id": ALG_ED25519,
        "key_id": int(key_id),
        "ledger_id_h64": hash64(ledger_id.encode("utf-8")),
        "origin_repo_h64": hash64(origin_repo.encode("utf-8")),
        "origin_node_h64": hash64(origin_node.encode("utf-8")),
        "subject_id_h64": hash64(subject.encode("utf-8")),
        "issuer_id_h64": hash64(issuer.encode("utf-8")),
        "nonce64": int(nonce64) & 0xFFFFFFFFFFFFFFFF,
        "prev_event_h64": int(prev_event_h64) & 0xFFFFFFFFFFFFFFFF,
        "payload_hash_h64": hash64(payload),
    }
    proof = private_key.sign(build_signing_input(header, trailer))
    return pack_envelope(header, payload, trailer, proof)


def _run() -> int:
    sync_base = os.getenv("SYNC_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    private_key_hex = os.getenv("E6_SYNC_PRIVATE_KEY_HEX", "").strip()
    key_id = int(os.getenv("E6_SYNC_KEY_ID", "1"))

    if not private_key_hex:
        print("Missing E6_SYNC_PRIVATE_KEY_HEX", file=sys.stderr)
        return 2
    if len(private_key_hex) != 64:
        print("E6_SYNC_PRIVATE_KEY_HEX must be 64 hex chars", file=sys.stderr)
        return 2

    ledger_id = os.getenv("E6_SYNC_LEDGER_ID", "ledger-local")
    origin_repo = os.getenv("E6_SYNC_ORIGIN_REPO", "openclaw-local")
    origin_node = os.getenv("E6_SYNC_ORIGIN_NODE", f"openclaw-{socket.gethostname()}-{secrets.token_hex(3)}")
    subject = os.getenv("E6_SYNC_SUBJECT", "prime:subject:openclaw")
    issuer = os.getenv("E6_SYNC_ISSUER", "prime:issuer:openclaw")

    checks: list[CheckResult] = []

    # P0.1 handshake compatibility
    handshake = _post_json(
        f"{sync_base}/sync/v0/handshake",
        {
            "peer_id": "openclaw-p0-harness",
            "protocol_versions": [0],
            "envelope_versions": [0],
            "alg_ids": [1, 2],
        },
    )
    alg_ids = list(handshake.get("alg_ids") or [])
    checks.append(
        CheckResult(
            name="handshake_alg_compat",
            ok=(bool(handshake.get("accepted")) and 1 in alg_ids),
            details=f"accepted={handshake.get('accepted')} alg_ids={alg_ids}",
        )
    )

    # P0.2+P0.3 signed append and chain continuity in fixed stream
    env1 = _build_signed_envelope(
        private_key_hex=private_key_hex,
        key_id=key_id,
        seq=1,
        prev_event_h64=0,
        ledger_id=ledger_id,
        origin_repo=origin_repo,
        origin_node=origin_node,
        subject=subject,
        issuer=issuer,
        nonce64=secrets.randbits(64),
        message="openclaw p0 seq1",
    )
    push1 = _post_json(
        f"{sync_base}/sync/v0/push",
        {"peer_id": "openclaw-p0-harness", "items": [{"envelope_hex": env1.hex()}]},
    )
    ok1 = int(push1.get("accepted", 0)) >= 1
    event1 = None
    if ok1:
        results = list(push1.get("results") or [])
        if results:
            event1 = str(results[0].get("event_id") or "")
    checks.append(CheckResult("push_seq1_accepted", ok1 and bool(event1), f"push={push1}"))

    if not event1:
        _print_results(checks)
        return 1

    env2 = _build_signed_envelope(
        private_key_hex=private_key_hex,
        key_id=key_id,
        seq=2,
        prev_event_h64=int(event1, 16),
        ledger_id=ledger_id,
        origin_repo=origin_repo,
        origin_node=origin_node,
        subject=subject,
        issuer=issuer,
        nonce64=secrets.randbits(64),
        message="openclaw p0 seq2",
    )
    push2 = _post_json(
        f"{sync_base}/sync/v0/push",
        {"peer_id": "openclaw-p0-harness", "items": [{"envelope_hex": env2.hex()}]},
    )
    ok2 = int(push2.get("accepted", 0)) >= 1
    checks.append(CheckResult("push_seq2_chain_ok", ok2, f"push={push2}"))

    # P0.4 duplicate/replay behavior
    push2_dup = _post_json(
        f"{sync_base}/sync/v0/push",
        {"peer_id": "openclaw-p0-harness", "items": [{"envelope_hex": env2.hex()}]},
    )
    dup_ok = int(push2_dup.get("duplicate", 0)) >= 1
    checks.append(CheckResult("duplicate_replay_detected", dup_ok, f"push={push2_dup}"))

    _print_results(checks)
    return 0 if all(c.ok for c in checks) else 1


def _print_results(checks: list[CheckResult]) -> None:
    print("OpenClaw P0 Harness Results")
    for c in checks:
        status = "PASS" if c.ok else "FAIL"
        print(f"- {status} {c.name}: {c.details}")


if __name__ == "__main__":
    raise SystemExit(_run())
