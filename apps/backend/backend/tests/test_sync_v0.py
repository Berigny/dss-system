from __future__ import annotations

import hashlib
import hmac
import json

import backend.api.sync as sync_module
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.sync import router as sync_router
from backend.fieldx_kernel.e6_envelope import (
    ALG_HMAC_SHA256,
    TRAILER_V0,
    TrailerV0,
    build_signing_input,
    hash64,
    pack_envelope_v0,
    verify_envelope_v0,
)
from backend.fieldx_kernel.e6_packet import pack_header_v0


def _build_signed_envelope(
    *,
    seq: int,
    nonce64: int,
    prev_event_h64: int,
    payload_text: str,
    secret: bytes = b"unit-test-sync-secret",
) -> bytes:
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
        seq=seq,
        t_ms=100 + seq,
        V_q=45000,
    )
    payload = json.dumps({"msg": payload_text, "seq": seq}, separators=(",", ":")).encode("utf-8")
    trailer = TrailerV0(
        trailer_ver=TRAILER_V0,
        alg_id=ALG_HMAC_SHA256,
        key_id=1,
        ledger_id_h64=0xABCD,
        origin_repo_h64=0x1234,
        origin_node_h64=0x5678,
        subject_id_h64=0xAAAA,
        issuer_id_h64=0xBBBB,
        nonce64=nonce64,
        prev_event_h64=prev_event_h64,
        payload_hash_h64=hash64(payload),
        proof=b"",
    )
    signing_input = build_signing_input(header, trailer)
    proof = hmac.new(secret, signing_input, hashlib.sha256).digest()[:16]
    trailer = TrailerV0(**{**trailer.__dict__, "proof": proof})
    return pack_envelope_v0(header=header, payload=payload, trailer=trailer)


def _ledger_scope_h64() -> str:
    return "000000000000abcd"


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("E6_SYNC_HMAC_KEY", "unit-test-sync-secret")
    app = FastAPI()
    app.state.db = {}
    app.include_router(sync_router)
    return TestClient(app)


def test_sync_v0_nonce_replay_quarantined(monkeypatch) -> None:
    client = _make_client(monkeypatch)

    env1 = _build_signed_envelope(seq=1, nonce64=0x9001, prev_event_h64=0, payload_text="one")
    push1 = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-a", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": env1.hex()}]},
    )
    assert push1.status_code == 200
    body1 = push1.json()
    assert body1["accepted"] == 1

    first_event = verify_envelope_v0(
        env1,
        hmac_key_resolver=lambda key_id: b"unit-test-sync-secret" if key_id == 1 else None,
    )
    assert first_event["ok"] is True
    prev_h64 = int(first_event["event_id"], 16)

    env2 = _build_signed_envelope(
        seq=2,
        nonce64=0x9001,  # replayed nonce
        prev_event_h64=prev_h64,
        payload_text="two",
    )
    push2 = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-a", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": env2.hex()}]},
    )
    assert push2.status_code == 200
    body2 = push2.json()
    assert body2["accepted"] == 0
    assert body2["quarantine"] == 1
    assert body2["results"][0]["reason"] == "nonce_replay"


def test_sync_v0_chain_mismatch_quarantined(monkeypatch) -> None:
    client = _make_client(monkeypatch)

    env1 = _build_signed_envelope(seq=1, nonce64=0x9101, prev_event_h64=0, payload_text="one")
    push1 = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-b", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": env1.hex()}]},
    )
    assert push1.status_code == 200
    assert push1.json()["accepted"] == 1

    env2 = _build_signed_envelope(
        seq=2,
        nonce64=0x9102,
        prev_event_h64=0xDEADBEEF,  # wrong predecessor
        payload_text="two",
    )
    push2 = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-b", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": env2.hex()}]},
    )
    assert push2.status_code == 200
    body2 = push2.json()
    assert body2["accepted"] == 0
    assert body2["quarantine"] == 1
    assert body2["results"][0]["reason"] == "chain_mismatch"


def test_sync_v0_missing_predecessor_quarantined(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    env = _build_signed_envelope(
        seq=1,
        nonce64=0x9151,
        prev_event_h64=0x1111,  # non-zero predecessor on empty stream
        payload_text="requires-prev",
    )
    push = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-missing-prev", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": env.hex()}]},
    )
    assert push.status_code == 200
    body = push.json()
    assert body["accepted"] == 0
    assert body["quarantine"] == 1
    assert body["results"][0]["reason"] == "missing_predecessor"


def test_sync_v0_allow_backfill_accepts_missing_predecessor(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    env = _build_signed_envelope(
        seq=1,
        nonce64=0x9152,
        prev_event_h64=0x2222,  # non-zero predecessor on empty stream
        payload_text="backfill",
    )
    push = client.post(
        "/sync/v0/push",
        json={
            "peer_id": "peer-backfill",
            "ledger_id_h64": _ledger_scope_h64(),
            "items": [{"envelope_hex": env.hex(), "allow_backfill": True}],
        },
    )
    assert push.status_code == 200
    body = push.json()
    assert body["accepted"] == 1
    assert body["quarantine"] == 0


def test_sync_v0_divergence_same_seq_quarantined(monkeypatch) -> None:
    client = _make_client(monkeypatch)

    env1 = _build_signed_envelope(seq=1, nonce64=0x9201, prev_event_h64=0, payload_text="one")
    push1 = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-c", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": env1.hex()}]},
    )
    assert push1.status_code == 200
    assert push1.json()["accepted"] == 1

    env2 = _build_signed_envelope(
        seq=1,  # conflicting seq in same stream
        nonce64=0x9202,
        prev_event_h64=0,
        payload_text="conflicting-one",
    )
    push2 = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-c", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": env2.hex()}]},
    )
    assert push2.status_code == 200
    body2 = push2.json()
    assert body2["accepted"] == 0
    assert body2["quarantine"] == 1
    assert body2["results"][0]["reason"] == "divergence_seq_conflict"


def test_sync_v0_bootstraps_missing_latest_from_stream_head(monkeypatch) -> None:
    client = _make_client(monkeypatch)

    env1 = _build_signed_envelope(seq=1, nonce64=0x9251, prev_event_h64=0, payload_text="one")
    push1 = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-bootstrap", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": env1.hex()}]},
    )
    assert push1.status_code == 200
    assert push1.json()["accepted"] == 1

    verified1 = verify_envelope_v0(
        env1,
        hmac_key_resolver=lambda key_id: b"unit-test-sync-secret" if key_id == 1 else None,
    )
    assert verified1["ok"] is True
    stream_key = str(verified1["stream_key"])
    prev_h64 = int(str(verified1["event_id"]), 16)

    latest_key = f"sync:v0:stream:{stream_key}:latest".encode("utf-8")
    client.app.state.db.pop(latest_key, None)

    env2 = _build_signed_envelope(
        seq=2,
        nonce64=0x9252,
        prev_event_h64=prev_h64,
        payload_text="two",
    )
    push2 = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-bootstrap", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": env2.hex()}]},
    )
    assert push2.status_code == 200
    body2 = push2.json()
    assert body2["accepted"] == 1
    assert body2["quarantine"] == 0


def test_sync_v0_handshake_advertises_ed25519_when_keys_configured(monkeypatch) -> None:
    monkeypatch.setenv("E6_SYNC_ED25519_KEYS", '{"7":"00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"}')
    client = _make_client(monkeypatch)
    resp = client.post(
        "/sync/v0/handshake",
        json={
            "peer_id": "peer-d",
            "protocol_versions": [0],
            "envelope_versions": [0],
            "alg_ids": [1, 2],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert 1 in body["alg_ids"]


def test_sync_v0_push_rejects_ledger_scope_mismatch(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    env1 = _build_signed_envelope(seq=1, nonce64=0x9301, prev_event_h64=0, payload_text="one")
    push = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-scope", "ledger_id_h64": "000000000000beef", "items": [{"envelope_hex": env1.hex()}]},
    )
    assert push.status_code == 200
    body = push.json()
    assert body["accepted"] == 0
    assert body["quarantine"] == 1
    assert body["results"][0]["reason"] == "ledger_scope_mismatch"


def test_sync_v0_push_requires_ledger_scope(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    env = _build_signed_envelope(seq=1, nonce64=0x9302, prev_event_h64=0, payload_text="one")
    push = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-scope", "items": [{"envelope_hex": env.hex()}]},
    )
    assert push.status_code == 422


def test_sync_v0_checkpoint_roundtrip(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    stream_key = f"{_ledger_scope_h64()}:1cf3cbde3ac92773:6f900f90c540efdd"

    save = client.post(
        "/sync/v0/checkpoint/save",
        json={
            "peer_id": "peer-checkpoint",
            "ledger_id_h64": _ledger_scope_h64(),
            "cursor_name": "default",
            "cursors": {stream_key: 7},
            "metadata": {"source": "unit-test"},
        },
    )
    assert save.status_code == 200
    save_body = save.json()
    assert save_body["saved"] is True
    assert save_body["cursor_count"] == 1

    load = client.post(
        "/sync/v0/checkpoint/load",
        json={
            "peer_id": "peer-checkpoint",
            "ledger_id_h64": _ledger_scope_h64(),
            "cursor_name": "default",
        },
    )
    assert load.status_code == 200
    load_body = load.json()
    assert load_body["exists"] is True
    assert load_body["cursors"][stream_key] == 7
    assert load_body["metadata"]["source"] == "unit-test"

    status = client.get("/sync/v0/status")
    assert status.status_code == 200
    assert int(status.json()["checkpoints"]) >= 1


def test_sync_v0_checkpoint_rejects_cross_ledger_cursor(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    wrong_stream_key = "000000000000beef:1cf3cbde3ac92773:6f900f90c540efdd"
    save = client.post(
        "/sync/v0/checkpoint/save",
        json={
            "peer_id": "peer-checkpoint",
            "ledger_id_h64": _ledger_scope_h64(),
            "cursor_name": "default",
            "cursors": {wrong_stream_key: 2},
        },
    )
    assert save.status_code == 400
    assert "checkpoint cursors must belong to ledger_id_h64" in save.text


def test_sync_v0_bad_proof_reason_is_canonical(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    env = bytearray(_build_signed_envelope(seq=1, nonce64=0x9401, prev_event_h64=0, payload_text="tamper"))
    env[-1] ^= 0x01  # break HMAC proof deterministically
    push = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-proof", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": bytes(env).hex()}]},
    )
    assert push.status_code == 200
    body = push.json()
    assert body["accepted"] == 0
    assert body["quarantine"] == 1
    assert body["results"][0]["reason"] == "bad_proof"


def test_sync_v0_unknown_verifier_reason_normalized(monkeypatch) -> None:
    client = _make_client(monkeypatch)

    def _fake_verify(*_args, **_kwargs):
        return {"ok": False, "reason": "unexpected_verifier_error"}

    monkeypatch.setattr(sync_module, "verify_envelope_v0", _fake_verify)
    env = _build_signed_envelope(seq=1, nonce64=0x9402, prev_event_h64=0, payload_text="one")
    push = client.post(
        "/sync/v0/push",
        json={"peer_id": "peer-verify", "ledger_id_h64": _ledger_scope_h64(), "items": [{"envelope_hex": env.hex()}]},
    )
    assert push.status_code == 200
    body = push.json()
    assert body["accepted"] == 0
    assert body["quarantine"] == 1
    assert body["results"][0]["reason"] == "verification_failed"
