#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import secrets
import socket
import sys
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

try:
    from utils.e6_packet import (
        build_signing_input,
        hash64,
        pack_envelope_v0 as pack_envelope,
        pack_header_v0,
    )
except ImportError:  # pragma: no cover - supports running the script directly from utils/
    from e6_packet import (
        build_signing_input,
        hash64,
        pack_envelope_v0 as pack_envelope,
        pack_header_v0,
    )

MAGIC_V0 = 0x4347
VERSION_V0 = 1
FORMAT_V0 = 0
ALG_ED25519 = 1
TRAILER_V0 = 0


def _post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url=url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=20) as resp:
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
        private_key_bytes = bytes.fromhex(private_hex)
    except ValueError:
        print("E6_SYNC_PRIVATE_KEY_HEX must be hex", file=sys.stderr)
        return 2
    if len(private_key_bytes) != 32:
        print("E6_SYNC_PRIVATE_KEY_HEX must be 32 bytes (64 hex chars)", file=sys.stderr)
        return 2

    seq = _env_int("E6_SYNC_SEQ", 1) & 0xFFFFFF
    key_id = _env_int("E6_SYNC_KEY_ID", 1)
    prev_event_h64 = _env_int("E6_SYNC_PREV_EVENT_H64", 0) & 0xFFFFFFFFFFFFFFFF
    nonce64 = _env_int("E6_SYNC_NONCE64", secrets.randbits(64)) & 0xFFFFFFFFFFFFFFFF

    ledger_id = os.getenv("E6_SYNC_LEDGER_ID", "ledger-local")
    origin_repo = os.getenv("E6_SYNC_ORIGIN_REPO", "ds-middleware-local")
    origin_node = os.getenv("E6_SYNC_ORIGIN_NODE", f"{socket.gethostname()}-{secrets.token_hex(4)}")
    subject = os.getenv("E6_SYNC_SUBJECT", "prime:subject:middleware")
    issuer = os.getenv("E6_SYNC_ISSUER", "prime:issuer:middleware")

    payload = json.dumps(
        {
            "kind": "WU",
            "msg": os.getenv("E6_SYNC_MESSAGE", "middleware ed25519 smoke"),
            "seq": seq,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

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
    trailer = {
        "alg_id": ALG_ED25519,
        "key_id": key_id,
        "ledger_id_h64": hash64(ledger_id.encode("utf-8")),
        "origin_repo_h64": hash64(origin_repo.encode("utf-8")),
        "origin_node_h64": hash64(origin_node.encode("utf-8")),
        "subject_id_h64": hash64(subject.encode("utf-8")),
        "issuer_id_h64": hash64(issuer.encode("utf-8")),
        "nonce64": nonce64,
        "prev_event_h64": prev_event_h64,
        "payload_hash_h64": hash64(payload),
    }

    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    signing_input = build_signing_input(header, trailer)
    proof = private_key.sign(signing_input)
    envelope = pack_envelope(header, payload, trailer, proof)

    handshake = _post_json(
        f"{base_url}/sync/v0/handshake",
        {
            "peer_id": "middleware-ed25519-smoke",
            "protocol_versions": [0],
            "envelope_versions": [0],
            "alg_ids": [1, 2],
        },
    )
    push = _post_json(
        f"{base_url}/sync/v0/push",
        {
            "peer_id": "middleware-ed25519-smoke",
            "items": [{"envelope_hex": envelope.hex()}],
        },
    )

    print("handshake:", json.dumps(handshake, separators=(",", ":")))
    print("push:", json.dumps(push, separators=(",", ":")))
    if int(push.get("accepted", 0)) < 1:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
