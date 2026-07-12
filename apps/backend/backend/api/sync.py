"""Ledger sync endpoints, including envelope-based v0 sync."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.fieldx_kernel.e6_envelope import verify_envelope_v0
from backend.services.authz import authorize_or_raise
from backend.services.ledger_service import LedgerService

router = APIRouter(prefix="/sync", tags=["sync"])

_SYNC_EVENT_PREFIX = b"sync:v0:event:"
_SYNC_STREAM_PREFIX = b"sync:v0:stream:"
_SYNC_NONCE_PREFIX = b"sync:v0:nonce:"
_SYNC_QUARANTINE_PREFIX = b"sync:v0:quarantine:"
_SYNC_CHECKPOINT_PREFIX = b"sync:v0:checkpoint:"

_REASON_DECODE_ERROR = "decode_error"
_REASON_VERIFICATION_FAILED = "verification_failed"
_REASON_LEDGER_SCOPE_MISMATCH = "ledger_scope_mismatch"
_REASON_NONCE_REPLAY = "nonce_replay"
_REASON_DIVERGENCE_SEQ_CONFLICT = "divergence_seq_conflict"
_REASON_MISSING_PREDECESSOR = "missing_predecessor"
_REASON_CHAIN_MISMATCH = "chain_mismatch"

_VERIFIER_REASON_WHITELIST = {
    "unsupported_trailer_ver",
    "bad_magic",
    "bad_crc",
    "payload_hash_mismatch",
    "missing_key_resolver",
    "unknown_key_id",
    "bad_proof",
    "invalid_public_key",
    "missing_ed25519_verifier",
    "unknown_alg",
}


class PushRequest(BaseModel):
    entries: List[Dict[str, Any]]


class SyncV0HandshakeRequest(BaseModel):
    peer_id: str = Field(default="unknown")
    protocol_versions: List[int] = Field(default_factory=lambda: [0])
    envelope_versions: List[int] = Field(default_factory=lambda: [0])
    alg_ids: List[int] = Field(default_factory=lambda: [2])
    requested_ledgers: List[str] = Field(default_factory=list)


class SyncV0PushItem(BaseModel):
    envelope_hex: str
    allow_backfill: bool = False


class SyncV0PushRequest(BaseModel):
    peer_id: str = Field(default="unknown")
    ledger_id_h64: str = Field(..., description="Required ledger scope as 64-bit hex")
    items: List[SyncV0PushItem] = Field(default_factory=list)


class SyncV0PullRequest(BaseModel):
    peer_id: str = Field(default="unknown")
    ledger_id_h64: str = Field(..., description="Required ledger scope as 64-bit hex")
    stream_keys: List[str] = Field(default_factory=list)
    cursors: Dict[str, int] = Field(default_factory=dict)
    limit: int = 100


class SyncV0BackfillRequest(BaseModel):
    peer_id: str = Field(default="unknown")
    ledger_id_h64: str = Field(..., description="Required ledger scope as 64-bit hex")
    stream_key: str
    from_seq: int = 0
    to_seq: int | None = None
    limit: int = 200


class SyncV0CheckpointLoadRequest(BaseModel):
    peer_id: str = Field(default="unknown")
    ledger_id_h64: str = Field(..., description="Required ledger scope as 64-bit hex")
    cursor_name: str = Field(default="default")


class SyncV0CheckpointSaveRequest(SyncV0CheckpointLoadRequest):
    cursors: Dict[str, int] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


def _to_bytes_key(text: str) -> bytes:
    return LedgerService.to_bytes_key(text)


def _normalize_ledger_h64(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value.startswith("0x"):
        value = value[2:]
    if not value:
        raise HTTPException(status_code=400, detail="ledger_id_h64 is required")
    try:
        parsed = int(value, 16)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="ledger_id_h64 must be valid hex") from exc
    if parsed < 0 or parsed > 0xFFFFFFFFFFFFFFFF:
        raise HTTPException(status_code=400, detail="ledger_id_h64 out of range")
    return f"{parsed:016x}"


def _load_hmac_keys() -> dict[int, bytes]:
    keys: dict[int, bytes] = {}
    mapping_raw = os.getenv("E6_SYNC_HMAC_KEYS", "").strip()
    if mapping_raw:
        try:
            parsed = json.loads(mapping_raw)
            if isinstance(parsed, dict):
                for key_id, secret in parsed.items():
                    try:
                        kid = int(str(key_id))
                    except Exception:
                        continue
                    if secret is None:
                        continue
                    keys[kid] = str(secret).encode("utf-8")
        except Exception:
            pass

    fallback_secret = os.getenv("E6_SYNC_HMAC_KEY", "")
    if 1 not in keys:
        keys[1] = fallback_secret.encode("utf-8")
    return keys


def _hmac_key_resolver(keys: dict[int, bytes]):
    def _resolve(key_id: int) -> bytes | None:
        return keys.get(int(key_id))

    return _resolve


def _load_ed25519_public_keys() -> dict[int, bytes]:
    keys: dict[int, bytes] = {}
    mapping_raw = os.getenv("E6_SYNC_ED25519_KEYS", "").strip()
    if not mapping_raw:
        return keys
    try:
        parsed = json.loads(mapping_raw)
    except Exception:
        return keys
    if not isinstance(parsed, dict):
        return keys

    for key_id, encoded in parsed.items():
        try:
            kid = int(str(key_id))
        except Exception:
            continue
        if encoded is None:
            continue
        value = str(encoded).strip()
        if not value:
            continue
        try:
            if value.startswith("hex:"):
                raw = bytes.fromhex(value[4:])
            else:
                raw = bytes.fromhex(value)
        except Exception:
            continue
        keys[kid] = raw
    return keys


def _ed25519_key_resolver(keys: dict[int, bytes]):
    def _resolve(key_id: int) -> bytes | None:
        return keys.get(int(key_id))

    return _resolve


def _seq_key(stream_key: str, seq: int) -> bytes:
    return _to_bytes_key(f"sync:v0:stream:{stream_key}:seq:{int(seq) & 0xFFFFFF:06x}")


def _event_key(event_id: str) -> bytes:
    return _to_bytes_key(f"sync:v0:event:{event_id}")


def _latest_hash_key(stream_key: str) -> bytes:
    return _to_bytes_key(f"sync:v0:stream:{stream_key}:latest")


def _nonce_key(issuer_h64: int, ledger_h64: int, nonce64: int) -> bytes:
    return _to_bytes_key(
        f"sync:v0:nonce:{issuer_h64:016x}:{ledger_h64:016x}:{nonce64:016x}"
    )


def _checkpoint_key(*, peer_id: str, ledger_id_h64: str, cursor_name: str) -> bytes:
    return _to_bytes_key(
        f"sync:v0:checkpoint:{ledger_id_h64}:{peer_id}:{cursor_name}"
    )


def _normalize_checkpoint_cursors(ledger_id_h64: str, cursors: Dict[str, int]) -> Dict[str, int]:
    normalized: Dict[str, int] = {}
    for raw_stream_key, raw_seq in (cursors or {}).items():
        stream_key = str(raw_stream_key)
        if not stream_key.startswith(f"{ledger_id_h64}:"):
            raise HTTPException(
                status_code=400,
                detail="checkpoint cursors must belong to ledger_id_h64",
            )
        try:
            seq = int(raw_seq)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="checkpoint cursor seq must be int") from exc
        normalized[stream_key] = seq
    return normalized


def _quarantine(service: LedgerService, *, reason: str, payload: dict[str, Any]) -> None:
    ts = int(time.time() * 1000)
    key = _to_bytes_key(f"sync:v0:quarantine:{ts}:{time.perf_counter_ns()}")
    service.set_json(
        key,
        {
            "ts_ms": ts,
            "reason": reason,
            "payload": payload,
        },
    )


def _normalize_verifier_reason(raw_reason: Any) -> tuple[str, str | None]:
    reason = str(raw_reason or "").strip()
    if reason in _VERIFIER_REASON_WHITELIST:
        return reason, None
    return _REASON_VERIFICATION_FAILED, (reason or None)


def _bootstrap_latest_event_id(service: LedgerService, stream_key: str) -> str | None:
    """Recover missing stream latest pointer from highest known seq event."""
    seq_prefix = _to_bytes_key(f"sync:v0:stream:{stream_key}:seq:")
    highest_seq = -1
    highest_event_id: str | None = None
    for raw_key in service.iter_prefix_keys(seq_prefix):
        key_text = raw_key.decode("utf-8")
        seq_hex = key_text.rsplit(":", 1)[-1]
        try:
            seq = int(seq_hex, 16)
        except Exception:
            continue
        event_raw = service.raw_get(raw_key)
        if event_raw is None:
            continue
        event_id = (
            event_raw.decode("utf-8")
            if isinstance(event_raw, (bytes, bytearray))
            else str(event_raw)
        )
        if seq > highest_seq:
            highest_seq = seq
            highest_event_id = event_id

    if highest_event_id:
        service.raw_set(_latest_hash_key(stream_key), highest_event_id.encode("utf-8"))
    return highest_event_id


@router.post("/push")
async def sync_push(payload: PushRequest, request: Request):
    """Legacy raw entry replication endpoint."""
    service = LedgerService.from_request(request)
    authorize_or_raise(request, ledger_id="default", action="sync.push")

    written = 0
    for raw in payload.entries:
        try:
            service.write_raw_entry(raw)
            written += 1
        except Exception:
            continue

    return {"status": "ok", "written": written}


@router.post("/v0/handshake")
async def sync_v0_handshake(payload: SyncV0HandshakeRequest):
    supported_protocols = [0]
    supported_envelopes = [0]
    supported_algs = [2]
    if _load_ed25519_public_keys():
        supported_algs.insert(0, 1)
    protocol_match = [v for v in payload.protocol_versions if v in supported_protocols]
    envelope_match = [v for v in payload.envelope_versions if v in supported_envelopes]
    alg_match = [v for v in payload.alg_ids if v in supported_algs]
    return {
        "status": "ok",
        "peer_id": payload.peer_id,
        "accepted": bool(protocol_match and envelope_match and alg_match),
        "protocol_versions": protocol_match,
        "envelope_versions": envelope_match,
        "alg_ids": alg_match,
        "constraints": {
            "cross_repo_requires_alg": 1,
            "max_batch": 500,
        },
    }


@router.post("/v0/push")
async def sync_v0_push(payload: SyncV0PushRequest, request: Request):
    service = LedgerService.from_request(request)
    ledger_scope = _normalize_ledger_h64(payload.ledger_id_h64)
    authorize_or_raise(request, ledger_id=ledger_scope, action="sync.push", explicit_context=True)

    if not payload.items:
        return {"status": "ok", "accepted": 0, "duplicate": 0, "quarantine": 0, "results": []}

    if len(payload.items) > 500:
        raise HTTPException(status_code=400, detail="batch too large; max 500")

    hmac_keys = _load_hmac_keys()
    ed25519_keys = _load_ed25519_public_keys()
    key_resolver = _hmac_key_resolver(hmac_keys)
    ed25519_resolver = _ed25519_key_resolver(ed25519_keys)

    accepted = 0
    duplicates = 0
    quarantined = 0
    results: list[dict[str, Any]] = []

    for item in payload.items:
        envelope_hex = item.envelope_hex.strip()
        try:
            envelope = bytes.fromhex(envelope_hex)
        except Exception:
            quarantined += 1
            reason = _REASON_DECODE_ERROR
            _quarantine(service, reason=reason, payload={"peer_id": payload.peer_id, "envelope_hex": envelope_hex})
            results.append({"status": "quarantine", "reason": reason})
            continue

        verified = verify_envelope_v0(
            envelope,
            hmac_key_resolver=key_resolver,
            ed25519_public_key_resolver=ed25519_resolver,
        )
        if not verified.get("ok"):
            quarantined += 1
            reason, detail = _normalize_verifier_reason(verified.get("reason"))
            quarantine_payload = {"peer_id": payload.peer_id, "envelope_hex": envelope_hex}
            if detail:
                quarantine_payload["reason_detail"] = detail
            _quarantine(service, reason=reason, payload=quarantine_payload)
            results.append({"status": "quarantine", "reason": reason})
            continue

        trailer = verified["trailer"]
        trailer_ledger_h64 = f"{int(trailer.ledger_id_h64) & 0xFFFFFFFFFFFFFFFF:016x}"
        if trailer_ledger_h64 != ledger_scope:
            quarantined += 1
            reason = _REASON_LEDGER_SCOPE_MISMATCH
            _quarantine(
                service,
                reason=reason,
                payload={
                    "peer_id": payload.peer_id,
                    "ledger_scope": ledger_scope,
                    "trailer_ledger_h64": trailer_ledger_h64,
                },
            )
            results.append({"status": "quarantine", "reason": reason})
            continue

        stream_key = str(verified["stream_key"])
        event_id = str(verified["event_id"])
        seq = int(verified["header"]["seq"])
        seq_storage_key = _seq_key(stream_key, seq)
        latest_storage_key = _latest_hash_key(stream_key)
        nonce_storage_key = _nonce_key(trailer.issuer_id_h64, trailer.ledger_id_h64, trailer.nonce64)

        if service.get_json(_event_key(event_id)) is not None:
            duplicates += 1
            results.append({"status": "duplicate", "event_id": event_id, "stream_key": stream_key, "seq": seq})
            continue

        if service.raw_get(nonce_storage_key) is not None:
            quarantined += 1
            reason = _REASON_NONCE_REPLAY
            _quarantine(
                service,
                reason=reason,
                payload={"peer_id": payload.peer_id, "event_id": event_id, "stream_key": stream_key, "seq": seq},
            )
            results.append({"status": "quarantine", "reason": reason, "event_id": event_id, "stream_key": stream_key, "seq": seq})
            continue

        seq_existing = service.raw_get(seq_storage_key)
        if seq_existing is not None:
            seq_existing_id = (
                seq_existing.decode("utf-8")
                if isinstance(seq_existing, (bytes, bytearray))
                else str(seq_existing)
            )
            if seq_existing_id != event_id:
                quarantined += 1
                reason = _REASON_DIVERGENCE_SEQ_CONFLICT
                _quarantine(
                    service,
                    reason=reason,
                    payload={
                        "peer_id": payload.peer_id,
                        "event_id": event_id,
                        "existing_event_id": seq_existing_id,
                        "stream_key": stream_key,
                        "seq": seq,
                    },
                )
                results.append({"status": "quarantine", "reason": reason, "event_id": event_id, "stream_key": stream_key, "seq": seq})
                continue
            duplicates += 1
            results.append({"status": "duplicate", "event_id": event_id, "stream_key": stream_key, "seq": seq})
            continue

        latest_raw = service.raw_get(latest_storage_key)
        latest_hash = (
            latest_raw.decode("utf-8")
            if isinstance(latest_raw, (bytes, bytearray))
            else str(latest_raw)
            if latest_raw is not None
            else None
        )
        if latest_hash is None:
            latest_hash = _bootstrap_latest_event_id(service, stream_key)
        expected_prev = f"{int(trailer.prev_event_h64) & 0xFFFFFFFFFFFFFFFF:016x}"
        if latest_hash is None:
            if trailer.prev_event_h64 != 0 and not item.allow_backfill:
                quarantined += 1
                reason = _REASON_MISSING_PREDECESSOR
                _quarantine(
                    service,
                    reason=reason,
                    payload={"peer_id": payload.peer_id, "event_id": event_id, "stream_key": stream_key, "seq": seq},
                )
                results.append({"status": "quarantine", "reason": reason, "event_id": event_id, "stream_key": stream_key, "seq": seq})
                continue
        elif latest_hash != expected_prev and not item.allow_backfill:
            quarantined += 1
            reason = _REASON_CHAIN_MISMATCH
            _quarantine(
                service,
                reason=reason,
                payload={
                    "peer_id": payload.peer_id,
                    "event_id": event_id,
                    "stream_key": stream_key,
                    "seq": seq,
                    "expected_prev": latest_hash,
                    "provided_prev": expected_prev,
                },
            )
            results.append({"status": "quarantine", "reason": reason, "event_id": event_id, "stream_key": stream_key, "seq": seq})
            continue

        ts = datetime.now(timezone.utc).isoformat()
        event_record = {
            "event_id": event_id,
            "stream_key": stream_key,
            "seq": seq,
            "header": verified["header"],
            "trailer": {
                "trailer_ver": trailer.trailer_ver,
                "alg_id": trailer.alg_id,
                "key_id": trailer.key_id,
                "ledger_id_h64": f"{trailer.ledger_id_h64:016x}",
                "origin_repo_h64": f"{trailer.origin_repo_h64:016x}",
                "origin_node_h64": f"{trailer.origin_node_h64:016x}",
                "subject_id_h64": f"{trailer.subject_id_h64:016x}",
                "issuer_id_h64": f"{trailer.issuer_id_h64:016x}",
                "nonce64": f"{trailer.nonce64:016x}",
                "prev_event_h64": f"{trailer.prev_event_h64:016x}",
                "payload_hash_h64": f"{trailer.payload_hash_h64:016x}",
            },
            "payload_hex": verified["payload"].hex(),
            "envelope_hex": envelope_hex,
            "created_at": ts,
            "peer_id": payload.peer_id,
        }
        service.set_json(_event_key(event_id), event_record)
        service.raw_set(seq_storage_key, event_id.encode("utf-8"))
        service.raw_set(latest_storage_key, event_id.encode("utf-8"))
        service.raw_set(nonce_storage_key, ts.encode("utf-8"))

        accepted += 1
        results.append({"status": "accepted", "event_id": event_id, "stream_key": stream_key, "seq": seq})

    return {
        "status": "ok",
        "accepted": accepted,
        "duplicate": duplicates,
        "quarantine": quarantined,
        "results": results,
    }


def _stream_keys_for_ledger(
    service: LedgerService, ledger_id_h64: str | None, explicit_stream_keys: list[str]
) -> list[str]:
    if explicit_stream_keys:
        return sorted(set(explicit_stream_keys))
    stream_keys: set[str] = set()
    for raw_key in service.iter_prefix_keys(_SYNC_STREAM_PREFIX):
        key_text = raw_key.decode("utf-8")
        if not key_text.endswith(":latest"):
            continue
        stream_key = key_text[len("sync:v0:stream:") : -len(":latest")]
        if ledger_id_h64 and not stream_key.startswith(f"{ledger_id_h64.lower()}:"):
            continue
        stream_keys.add(stream_key)
    return sorted(stream_keys)


def _load_event_for_stream_seq(service: LedgerService, stream_key: str, seq: int) -> dict[str, Any] | None:
    seq_raw = service.raw_get(_seq_key(stream_key, seq))
    if seq_raw is None:
        return None
    event_id = seq_raw.decode("utf-8") if isinstance(seq_raw, (bytes, bytearray)) else str(seq_raw)
    return service.get_json(_event_key(event_id))


@router.post("/v0/pull")
async def sync_v0_pull(payload: SyncV0PullRequest, request: Request):
    service = LedgerService.from_request(request)
    ledger_filter = _normalize_ledger_h64(payload.ledger_id_h64)
    authorize_or_raise(request, ledger_id=ledger_filter, action="sync.pull", explicit_context=True)

    limit = max(1, min(int(payload.limit), 500))
    if payload.stream_keys:
        for stream_key in payload.stream_keys:
            if not str(stream_key).startswith(f"{ledger_filter}:"):
                raise HTTPException(
                    status_code=400,
                    detail="stream_keys must belong to requested ledger_id_h64",
                )

    items: list[dict[str, Any]] = []
    stream_keys = _stream_keys_for_ledger(service, ledger_filter, payload.stream_keys)
    for stream_key in stream_keys:
        # If no cursor exists yet for this stream, begin at seq=1.
        has_cursor = stream_key in payload.cursors
        after_seq = int(payload.cursors.get(stream_key, 0))
        next_seq = after_seq + 1 if has_cursor else 1
        while len(items) < limit:
            record = _load_event_for_stream_seq(service, stream_key, next_seq)
            if record is None:
                break
            items.append(
                {
                    "event_id": record.get("event_id"),
                    "stream_key": stream_key,
                    "seq": next_seq,
                    "envelope_hex": record.get("envelope_hex"),
                    "created_at": record.get("created_at"),
                }
            )
            next_seq += 1
        if len(items) >= limit:
            break

    next_cursors = dict(payload.cursors)
    for item in items:
        try:
            seq = int(item["seq"])
            stream_key = str(item["stream_key"])
        except Exception:
            continue
        previous = int(next_cursors.get(stream_key, -1))
        if seq > previous:
            next_cursors[stream_key] = seq

    return {
        "status": "ok",
        "peer_id": payload.peer_id,
        "count": len(items),
        "items": items,
        "next_cursors": next_cursors,
    }


@router.post("/v0/backfill")
async def sync_v0_backfill(payload: SyncV0BackfillRequest, request: Request):
    service = LedgerService.from_request(request)
    ledger_filter = _normalize_ledger_h64(payload.ledger_id_h64)
    authorize_or_raise(request, ledger_id=ledger_filter, action="sync.pull", explicit_context=True)
    if not str(payload.stream_key).startswith(f"{ledger_filter}:"):
        raise HTTPException(status_code=400, detail="stream_key does not belong to ledger_id_h64")

    start_seq = max(0, int(payload.from_seq))
    end_seq = int(payload.to_seq) if payload.to_seq is not None else start_seq + max(1, min(int(payload.limit), 500))
    if end_seq < start_seq:
        raise HTTPException(status_code=400, detail="to_seq must be >= from_seq")

    items: list[dict[str, Any]] = []
    for seq in range(start_seq, end_seq + 1):
        if len(items) >= payload.limit:
            break
        record = _load_event_for_stream_seq(service, payload.stream_key, seq)
        if record is None:
            continue
        items.append(
            {
                "event_id": record.get("event_id"),
                "stream_key": payload.stream_key,
                "seq": seq,
                "envelope_hex": record.get("envelope_hex"),
                "created_at": record.get("created_at"),
            }
        )

    return {
        "status": "ok",
        "peer_id": payload.peer_id,
        "stream_key": payload.stream_key,
        "count": len(items),
        "items": items,
    }


@router.get("/v0/status")
async def sync_v0_status(request: Request):
    service = LedgerService.from_request(request)

    events = sum(1 for _ in service.iter_prefix_keys(_SYNC_EVENT_PREFIX))
    streams = sum(1 for key in service.iter_prefix_keys(_SYNC_STREAM_PREFIX) if key.endswith(b":latest"))
    nonces = sum(1 for _ in service.iter_prefix_keys(_SYNC_NONCE_PREFIX))
    quarantined = sum(1 for _ in service.iter_prefix_keys(_SYNC_QUARANTINE_PREFIX))
    checkpoints = sum(1 for _ in service.iter_prefix_keys(_SYNC_CHECKPOINT_PREFIX))
    return {
        "status": "ok",
        "events": events,
        "streams": streams,
        "nonces": nonces,
        "quarantine": quarantined,
        "checkpoints": checkpoints,
    }


@router.post("/v0/checkpoint/save")
async def sync_v0_checkpoint_save(payload: SyncV0CheckpointSaveRequest, request: Request):
    service = LedgerService.from_request(request)
    ledger_scope = _normalize_ledger_h64(payload.ledger_id_h64)
    authorize_or_raise(
        request,
        ledger_id=ledger_scope,
        action="sync.checkpoint.write",
        explicit_context=True,
    )
    peer_id = str(payload.peer_id or "unknown").strip() or "unknown"
    cursor_name = str(payload.cursor_name or "default").strip() or "default"
    cursors = _normalize_checkpoint_cursors(ledger_scope, payload.cursors)
    metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
    now_iso = datetime.now(timezone.utc).isoformat()
    checkpoint = {
        "peer_id": peer_id,
        "ledger_id_h64": ledger_scope,
        "cursor_name": cursor_name,
        "cursors": cursors,
        "metadata": metadata,
        "updated_at": now_iso,
    }
    service.set_json(
        _checkpoint_key(peer_id=peer_id, ledger_id_h64=ledger_scope, cursor_name=cursor_name),
        checkpoint,
    )
    return {
        "status": "ok",
        "saved": True,
        "peer_id": peer_id,
        "ledger_id_h64": ledger_scope,
        "cursor_name": cursor_name,
        "cursor_count": len(cursors),
        "updated_at": now_iso,
    }


@router.post("/v0/checkpoint/load")
async def sync_v0_checkpoint_load(payload: SyncV0CheckpointLoadRequest, request: Request):
    service = LedgerService.from_request(request)
    ledger_scope = _normalize_ledger_h64(payload.ledger_id_h64)
    authorize_or_raise(
        request,
        ledger_id=ledger_scope,
        action="sync.checkpoint.read",
        explicit_context=True,
    )
    peer_id = str(payload.peer_id or "unknown").strip() or "unknown"
    cursor_name = str(payload.cursor_name or "default").strip() or "default"
    checkpoint = service.get_json(
        _checkpoint_key(peer_id=peer_id, ledger_id_h64=ledger_scope, cursor_name=cursor_name)
    )
    if checkpoint is None:
        return {
            "status": "ok",
            "exists": False,
            "peer_id": peer_id,
            "ledger_id_h64": ledger_scope,
            "cursor_name": cursor_name,
            "cursors": {},
            "metadata": {},
        }
    return {
        "status": "ok",
        "exists": True,
        "peer_id": peer_id,
        "ledger_id_h64": ledger_scope,
        "cursor_name": cursor_name,
        "cursors": checkpoint.get("cursors") if isinstance(checkpoint.get("cursors"), dict) else {},
        "metadata": checkpoint.get("metadata") if isinstance(checkpoint.get("metadata"), dict) else {},
        "updated_at": checkpoint.get("updated_at"),
    }
