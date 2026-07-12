#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import secrets
import socket
import sys
from urllib.request import Request, urlopen

from backend.fieldx_kernel.e6_envelope import (
    ALG_ED25519,
    TRAILER_V0,
    TrailerV0,
    hash64,
    pack_envelope_v0,
    sign_trailer_v0_ed25519,
)
from backend.fieldx_kernel.e6_packet import pack_header_v0


def _post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url=url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip(), 0)


def main() -> int:
    base_url = os.getenv("SYNC_BASE_URL", "").rstrip("/")
    private_hex = os.getenv("E6_SYNC_PRIVATE_KEY_HEX", "").strip()
    if not private_hex:
        print("Missing E6_SYNC_PRIVATE_KEY_HEX", file=sys.stderr)
        return 2

    try:
        private_key = bytes.fromhex(private_hex)
    except ValueError:
        print("E6_SYNC_PRIVATE_KEY_HEX must be hex", file=sys.stderr)
        return 2
    if len(private_key) != 32:
        print("E6_SYNC_PRIVATE_KEY_HEX must be 32 bytes (64 hex chars)", file=sys.stderr)
        return 2

    key_id = _env_int("E6_SYNC_KEY_ID", 1)
    seq = _env_int("E6_SYNC_SEQ", 1) & 0xFFFFFF
    prev_event_h64 = _env_int("E6_SYNC_PREV_EVENT_H64", 0) & 0xFFFFFFFFFFFFFFFF
    nonce64 = _env_int("E6_SYNC_NONCE64", secrets.randbits(64)) & 0xFFFFFFFFFFFFFFFF

    ledger_id = os.getenv("E6_SYNC_LEDGER_ID", "ledger-local")
    origin_repo = os.getenv("E6_SYNC_ORIGIN_REPO", "ds-backend-local")
    origin_node = os.getenv("E6_SYNC_ORIGIN_NODE", f"{socket.gethostname()}-{secrets.token_hex(4)}")
    subject = os.getenv("E6_SYNC_SUBJECT", "prime:subject:local")
    issuer = os.getenv("E6_SYNC_ISSUER", "prime:issuer:local")

    payload_obj = {
        "kind": "WU",
        "msg": os.getenv("E6_SYNC_MESSAGE", "ed25519 smoke event"),
        "seq": seq,
    }
    payload = json.dumps(payload_obj, separators=(",", ":"), sort_keys=True).encode("utf-8")

    header = pack_header_v0(
        mode=2,
        ptype=0,
        law=2,
        route=3,
        node=_env_int("E6_SYNC_NODE", 4),
        K=1,
        P=1,
        E=1,
        valid=1,
        dW=0,
        seq=seq,
        t_ms=_env_int("E6_SYNC_T_MS", 0),
        V_q=_env_int("E6_SYNC_V_Q", 45000),
    )
    unsigned_trailer = TrailerV0(
        trailer_ver=TRAILER_V0,
        alg_id=ALG_ED25519,
        key_id=key_id,
        ledger_id_h64=hash64(ledger_id.encode("utf-8")),
        origin_repo_h64=hash64(origin_repo.encode("utf-8")),
        origin_node_h64=hash64(origin_node.encode("utf-8")),
        subject_id_h64=hash64(subject.encode("utf-8")),
        issuer_id_h64=hash64(issuer.encode("utf-8")),
        nonce64=nonce64,
        prev_event_h64=prev_event_h64,
        payload_hash_h64=hash64(payload),
        proof=b"",
    )
    signed_trailer = sign_trailer_v0_ed25519(
        header=header,
        trailer=unsigned_trailer,
        private_key_bytes=private_key,
    )
    envelope = pack_envelope_v0(header=header, payload=payload, trailer=signed_trailer)

    handshake = _post_json(
        f"{base_url}/sync/v0/handshake",
        {
            "peer_id": "ed25519-smoke",
            "protocol_versions": [0],
            "envelope_versions": [0],
            "alg_ids": [1, 2],
        },
    )
    push = _post_json(
        f"{base_url}/sync/v0/push",
        {
            "peer_id": "ed25519-smoke",
            "ledger_id_h64": f"{signed_trailer.ledger_id_h64:016x}",
            "items": [{"envelope_hex": envelope.hex()}],
        },
    )

    print("handshake:", json.dumps(handshake, separators=(",", ":")))
    print("push:", json.dumps(push, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
