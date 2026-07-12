from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from utils.e6_packet import build_signing_input, hash64, pack_envelope_v0, pack_header_v0
from utils.execution_governor import ExecutionGovernor


def _normalize_h64(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value.startswith("0x"):
        value = value[2:]
    parsed = int(value, 16)
    if parsed < 0 or parsed > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("h64 out of range")
    return f"{parsed:016x}"


def _to_h64(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = "default"
    return f"{hash64(text.encode('utf-8')):016x}"


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return lo if value < lo else hi if value > hi else value


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    return default


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _lookup_nested(data: dict[str, Any], paths: list[tuple[str, ...]]) -> Any:
    for path in paths:
        cur: Any = data
        ok = True
        for key in path:
            if not isinstance(cur, dict):
                ok = False
                break
            cur = cur.get(key)
        if ok and cur is not None:
            return cur
    return None


def _decode_envelope_payload(envelope_hex: str) -> dict[str, Any]:
    value = str(envelope_hex or "").strip()
    if not value:
        return {}
    try:
        raw = bytes.fromhex(value)
    except Exception:
        return {}
    if len(raw) < 18:
        return {}
    payload_len = int.from_bytes(raw[16:18], "big")
    if len(raw) < 18 + payload_len:
        return {}
    payload_bytes = raw[18 : 18 + payload_len]
    payload_text = payload_bytes.decode("utf-8", errors="ignore")
    payload_json: dict[str, Any] | None = None
    try:
        parsed = json.loads(payload_text)
        if isinstance(parsed, dict):
            payload_json = parsed
    except Exception:
        payload_json = None

    trailer_raw = raw[18 + payload_len :]
    prev_event_h64 = ""
    if len(trailer_raw) >= 1 + 1 + 4 + 8 * 7:
        # trailer_ver(1), alg_id(1), key_id(4), then u64 fields...
        offset = 0
        offset += 1
        offset += 1
        offset += 4
        offset += 8  # ledger
        offset += 8  # origin_repo
        offset += 8  # origin_node
        offset += 8  # subject
        offset += 8  # issuer
        offset += 8  # nonce
        prev_event_h64 = trailer_raw[offset : offset + 8].hex()

    return {
        "payload_text": payload_text,
        "payload_json": payload_json,
        "prev_event_h64": prev_event_h64,
    }


def _extract_coord_candidates(payload_text: str, payload_json: dict[str, Any] | None) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        v = value.strip()
        if not v or v in seen:
            return
        seen.add(v)
        found.append(v)

    if payload_json:
        for key in ("coordinate", "coord"):
            add(payload_json.get(key))
        for key in ("coordinates", "coords", "context_coords"):
            arr = payload_json.get(key)
            if isinstance(arr, list):
                for item in arr:
                    add(item)

    for match in re.findall(r"\b[0-9a-f]{16}:[0-9a-f]{16}:[0-9a-f]{16}\b", payload_text.lower()):
        add(match)

    return found


class DSMCPServer:
    def __init__(self, *, backend_base: str, timeout_s: float = 20.0):
        self.backend_base = backend_base.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.pipeline_timeout_s = float(os.getenv("MCP_PIPELINE_TIMEOUT_S", "90"))
        self.peer_id = os.getenv("MCP_SYNC_PEER_ID", "chatgpt-mcp")
        self.default_ledger = os.getenv("DEFAULT_LEDGER_ID", "default")
        self.origin_repo = os.getenv("E6_SYNC_ORIGIN_REPO", "ds-middleware-local")
        self.origin_node = os.getenv("E6_SYNC_ORIGIN_NODE", f"mcp-{secrets.token_hex(4)}")
        self.issuer = os.getenv("E6_SYNC_ISSUER", "prime:issuer:mcp")
        self.subject = os.getenv("E6_SYNC_SUBJECT", "prime:subject:mcp")
        self.key_id = int(os.getenv("E6_SYNC_KEY_ID", "1"))
        self.queue_path = Path(os.getenv("MCP_QUEUE_PATH", ".mcp_sync_queue.jsonl"))
        self.state_path = Path(os.getenv("MCP_STREAM_STATE_PATH", ".mcp_stream_state.json"))
        self.middleware_base = os.getenv("MCP_MIDDLEWARE_BASE_URL", "").strip().rstrip("/") or os.getenv("MCP_FALLBACK_MIDDLEWARE_BASE_URL", "")
        self.append_pipeline_enabled = os.getenv("MCP_APPEND_PIPELINE", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.auto_e6_enabled = os.getenv("MCP_AUTO_E6", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.default_context_id = os.getenv("MCP_CONTEXT_ID", "ctx:mcp")
        adaptive_markers_raw = os.getenv("ADAPTIVE_EXECUTION_LOCAL_PROVIDER_MARKERS", "ollama,llama,local")
        adaptive_markers = tuple(
            marker.strip().lower() for marker in adaptive_markers_raw.split(",") if marker and marker.strip()
        )
        self.execution_governor = ExecutionGovernor(
            enabled=os.getenv("ADAPTIVE_EXECUTION_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"},
            force_profile=os.getenv("ADAPTIVE_EXECUTION_FORCE_PROFILE", ""),
            local_provider_markers=adaptive_markers or ("ollama", "llama", "local"),
        )
        self.tool_scopes: dict[str, set[str]] = {
            "ds.handshake": {"ds:read"},
            "ds.sync_status": {"ds:read"},
            "ds.verify": {"ds:read"},
            "ds.verify_coord": {"ds:read"},
            "ds.auto_rate_coord": {"ds:write"},
            "ds.introspect": {"ds:read"},
            "ds.append_event": {"ds:write"},
            "ds.sync_flush": {"ds:write"},
        }

    def _load_private_key(self) -> Ed25519PrivateKey:
        private_hex = os.getenv("E6_SYNC_PRIVATE_KEY_HEX", "").strip()
        if not private_hex:
            raise RuntimeError("E6_SYNC_PRIVATE_KEY_HEX is required for ds.append_event")
        private_key_bytes = bytes.fromhex(private_hex)
        if len(private_key_bytes) != 32:
            raise RuntimeError("E6_SYNC_PRIVATE_KEY_HEX must be 32 bytes (64 hex chars)")
        return Ed25519PrivateKey.from_private_bytes(private_key_bytes)

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"streams": {}, "events": {}}
        try:
            data = json.loads(self.state_path.read_text())
            return data if isinstance(data, dict) else {"streams": {}, "events": {}}
        except Exception:
            return {"streams": {}, "events": {}}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, separators=(",", ":"), sort_keys=True))

    def _get_stream_state(self, stream_key: str) -> tuple[int, str]:
        state = self._load_state()
        streams = state.get("streams") if isinstance(state.get("streams"), dict) else {}
        row = streams.get(stream_key) if isinstance(streams.get(stream_key), dict) else {}
        next_seq = int(row.get("next_seq") or 1)
        prev_event_h64 = str(row.get("prev_event_h64") or "0000000000000000")
        return max(1, next_seq), _normalize_h64(prev_event_h64)

    def _set_stream_state(self, stream_key: str, *, next_seq: int, prev_event_h64: str) -> None:
        state = self._load_state()
        streams = state.get("streams") if isinstance(state.get("streams"), dict) else {}
        streams[stream_key] = {
            "next_seq": int(next_seq),
            "prev_event_h64": _normalize_h64(prev_event_h64),
            "updated_at": int(time.time()),
        }
        state["streams"] = streams
        self._save_state(state)

    def _remember_event(self, *, event_id: str, stream_key: str, seq: int, ledger_id_h64: str) -> None:
        eid = str(event_id or "").strip().lower()
        if not eid:
            return
        state = self._load_state()
        events = state.get("events") if isinstance(state.get("events"), dict) else {}
        events[eid] = {
            "stream_key": str(stream_key),
            "seq": int(seq),
            "ledger_id_h64": str(ledger_id_h64).lower(),
            "updated_at": int(time.time()),
        }
        state["events"] = events
        self._save_state(state)

    def _lookup_event_index(self, event_id: str) -> dict[str, Any] | None:
        eid = str(event_id or "").strip().lower()
        if not eid:
            return None
        state = self._load_state()
        events = state.get("events") if isinstance(state.get("events"), dict) else {}
        row = events.get(eid)
        return row if isinstance(row, dict) else None

    async def _lookup_event_remote(self, *, ledger_h64: str, event_id: str) -> dict[str, Any] | None:
        target = str(event_id or "").strip().lower()
        if not target:
            return None
        cursors: dict[str, int] = {}
        for _ in range(20):
            body = await self._post(
                "/sync/v0/pull",
                {
                    "peer_id": self.peer_id,
                    "ledger_id_h64": ledger_h64,
                    "cursors": cursors,
                    "limit": 500,
                },
            )
            items = body.get("items") if isinstance(body.get("items"), list) else []
            if not items:
                return None
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("event_id") or "").lower() != target:
                    continue
                stream_key = str(item.get("stream_key") or "")
                try:
                    seq = int(item.get("seq"))
                except Exception:
                    continue
                row = {
                    "stream_key": stream_key,
                    "seq": seq,
                    "ledger_id_h64": ledger_h64,
                }
                self._remember_event(event_id=target, stream_key=stream_key, seq=seq, ledger_id_h64=ledger_h64)
                return row
            next_cursors = body.get("next_cursors") if isinstance(body.get("next_cursors"), dict) else {}
            if not next_cursors or next_cursors == cursors:
                return None
            normalized: dict[str, int] = {}
            for k, v in next_cursors.items():
                try:
                    normalized[str(k)] = int(v)
                except Exception:
                    continue
            if not normalized:
                return None
            cursors = normalized
        return None

    def _enqueue(self, item: dict[str, Any]) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        with self.queue_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, separators=(",", ":"), sort_keys=True))
            fh.write("\n")

    def _read_queue(self) -> list[dict[str, Any]]:
        if not self.queue_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.queue_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    rows.append(parsed)
            except Exception:
                continue
        return rows

    def _write_queue(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            if self.queue_path.exists():
                self.queue_path.unlink()
            return
        self.queue_path.write_text(
            "\n".join(json.dumps(row, separators=(",", ":"), sort_keys=True) for row in rows) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _decode_response_json(resp: httpx.Response) -> dict[str, Any]:
        if not resp.content:
            return {}
        text = resp.text or ""
        try:
            parsed = resp.json()
        except Exception as exc:
            preview = text.strip().replace("\n", " ")[:220]
            ctype = resp.headers.get("content-type", "")
            raise RuntimeError(
                f"non_json_response status={resp.status_code} content_type={ctype} body_preview={preview!r}"
            ) from exc
        if not isinstance(parsed, dict):
            preview = text.strip().replace("\n", " ")[:220]
            raise RuntimeError(
                f"invalid_json_shape status={resp.status_code} body_preview={preview!r}"
            )
        return parsed

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = httpx.Timeout(self.timeout_s)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.backend_base}{path}", json=payload)
            body = self._decode_response_json(resp)
            if resp.status_code >= 400:
                raise RuntimeError(f"{path} failed: {resp.status_code} {body}")
            return body

    async def _get(self, path: str) -> dict[str, Any]:
        timeout = httpx.Timeout(self.timeout_s)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{self.backend_base}{path}")
            body = self._decode_response_json(resp)
            if resp.status_code >= 400:
                raise RuntimeError(f"{path} failed: {resp.status_code} {body}")
            return body

    async def _post_middleware(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = httpx.Timeout(self.pipeline_timeout_s)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.middleware_base}{path}", json=payload)
            body = self._decode_response_json(resp)
            if resp.status_code >= 400:
                raise RuntimeError(f"middleware {path} failed: {resp.status_code} {body}")
            return body

    async def _get_backend_introspection(
        self,
        *,
        entity: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if isinstance(entity, str) and entity.strip():
            params["entity"] = entity.strip()
        if isinstance(session_id, str) and session_id.strip():
            params["session_id"] = session_id.strip()

        timeout = httpx.Timeout(self.timeout_s)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{self.backend_base}/api/chat/introspect",
                params=params,
            )
            if resp.status_code == 404:
                return {}
            body = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                raise RuntimeError(f"/api/chat/introspect failed: {resp.status_code} {body}")
            return body if isinstance(body, dict) else {}

    async def _run_turn_pipeline(self, *, args: dict[str, Any], payload_obj: dict[str, Any], ledger_h64: str) -> dict[str, Any]:
        message = ""
        for key in ("user_message", "message", "content", "msg", "text"):
            value = payload_obj.get(key)
            if isinstance(value, str) and value.strip():
                message = value.strip()
                break
        if not message:
            message = json.dumps(payload_obj, separators=(",", ":"), sort_keys=True)

        history_raw = payload_obj.get("history")
        history = history_raw if isinstance(history_raw, list) else []
        provider = args.get("provider") or payload_obj.get("provider")
        session_id = (
            str(args.get("session_id") or payload_obj.get("session_id") or f"mcp-{self.peer_id}").strip()
            or f"mcp-{self.peer_id}"
        )

        metadata_raw = payload_obj.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        metadata.update(
            {
                "source": "mcp",
                "mcp_tool": "ds.append_event",
                "ledger_id_h64": ledger_h64,
            }
        )

        chat_payload: dict[str, Any] = {
            "message": message,
            "history": history,
            "session_id": session_id,
            "enable_ledger": True,
            "metadata": metadata,
        }
        if isinstance(provider, str) and provider.strip():
            chat_payload["provider"] = provider.strip()

        return await self._post_middleware("/api/chat", chat_payload)

    def _derive_e6_header_fields(
        self,
        *,
        args: dict[str, Any],
        payload_obj: dict[str, Any],
        pipeline_result: dict[str, Any] | None,
        introspection_post: dict[str, Any] | None,
        queue_depth: int,
    ) -> dict[str, int]:
        provider_raw = args.get("provider") or payload_obj.get("provider") or ""
        provider = str(provider_raw)
        network_pressure_hint = _lookup_nested(
            introspection_post or {},
            [
                ("network_pressure",),
                ("adaptive", "network_pressure"),
                ("metrics", "network_pressure"),
            ],
        )
        decision = self.execution_governor.decide(
            provider=provider,
            enable_ledger=True,
            network_pressure_hint=_as_float(network_pressure_hint, default=0.0),
        )

        profile = (decision.profile or "FULL").upper()
        mode_by_profile = {"MINIMAL": 1, "FAST": 2, "FULL": 3}
        ptype_by_profile = {"MINIMAL": 1, "FAST": 2, "FULL": 0}
        mode = mode_by_profile.get(profile, 2)
        ptype = ptype_by_profile.get(profile, 2)

        law_raw = _lookup_nested(
            introspection_post or {},
            [
                ("e6", "law"),
                ("e6", "lawfulness"),
                ("law",),
                ("lawfulness",),
                ("adaptive_execution", "law"),
            ],
        )
        law = _clamp_int(int(_as_float(law_raw, default=2.0)), 0, 3)
        if decision.defer_guardian and law > 2:
            law = 2

        e_raw = _lookup_nested(introspection_post or {}, [("e6", "E"), ("flags", "E"), ("E",)])
        p_raw = _lookup_nested(introspection_post or {}, [("e6", "P"), ("flags", "P"), ("P",)])
        k_raw = _lookup_nested(introspection_post or {}, [("e6", "K"), ("flags", "K"), ("K",)])
        E = 1 if _as_bool(e_raw, default=(law >= 2)) else 0
        P = 1 if _as_bool(p_raw, default=True) else 0
        K = 1 if _as_bool(k_raw, default=True) else 0

        route = 3
        if E == 0 or law < 2:
            route = 1
        elif profile == "MINIMAL":
            route = 2

        v_raw = _lookup_nested(
            introspection_post or {},
            [
                ("e6", "V_q"),
                ("e6", "V"),
                ("adaptive_execution", "pressure"),
                ("pressure",),
            ],
        )
        if v_raw is None:
            v_raw = _lookup_nested(pipeline_result or {}, [("stats", "latency_ms"), ("timing_ms",)])
        if isinstance(v_raw, (int, float)) and 0.0 <= float(v_raw) <= 1.0:
            V_q = _clamp_int(int(float(v_raw) * 65535.0), 0, 65535)
        elif isinstance(v_raw, (int, float)) and 0.0 <= float(v_raw) <= 100.0:
            V_q = _clamp_int(int((100.0 - float(v_raw)) / 100.0 * 65535.0), 0, 65535)
        else:
            V_q = 45000

        dW = 0
        if queue_depth > 0:
            dW = _clamp_int(min(queue_depth, 12), -128, 127)

        return {
            "mode": mode,
            "ptype": ptype,
            "law": law,
            "route": route,
            "node": 4,
            "K": K,
            "P": P,
            "E": E,
            "valid": 1,
            "dW": dW,
            "V_q": V_q,
        }

    def required_scopes_for_rpc(self, payload: dict[str, Any]) -> set[str]:
        method = str(payload.get("method") or "")
        if method in {"initialize", "notifications/initialized"}:
            return set()
        if method == "tools/list":
            return {"ds:read"}
        if method == "tools/call":
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            name = str(params.get("name") or "")
            return set(self.tool_scopes.get(name) or set())
        return set()

    def _tool_defs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "ds.handshake",
                "description": "Check sync protocol compatibility and advertised algorithms.",
                "securitySchemes": [{"type": "oauth2", "scopes": ["ds:read"]}],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "peer_id": {"type": "string"},
                        "alg_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "default": [1, 2],
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "ds.append_event",
                "description": "Sign and push a sync envelope. Queues locally if backend unavailable.",
                "securitySchemes": [{"type": "oauth2", "scopes": ["ds:write"]}],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "payload": {"type": "object"},
                        "ledger_id": {"type": "string"},
                        "ledger_id_h64": {"type": "string"},
                        "ledger_id": {"type": "string"},
                        "subject": {"type": "string"},
                        "issuer": {"type": "string"},
                        "allow_backfill": {"type": "boolean", "default": False},
                        "queue_on_failure": {"type": "boolean", "default": True},
                        "skip_pipeline": {"type": "boolean", "default": False},
                        "include_introspection": {"type": "boolean", "default": True},
                        "auto_e6": {"type": "boolean", "default": True},
                        "provider": {"type": "string"},
                        "session_id": {"type": "string"},
                        "seq": {"type": "integer"},
                        "prev_event_h64": {"type": "string"},
                    },
                    "required": ["payload"],
                    "additionalProperties": True,
                },
            },
            {
                "name": "ds.sync_status",
                "description": "Get backend sync counters and local queue depth.",
                "securitySchemes": [{"type": "oauth2", "scopes": ["ds:read"]}],
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "ds.sync_flush",
                "description": "Flush queued envelopes to backend and persist checkpoint metadata.",
                "securitySchemes": [{"type": "oauth2", "scopes": ["ds:write"]}],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ledger_id": {"type": "string"},
                        "ledger_id_h64": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "ds.verify",
                "description": "Verify event presence and provide chain-of-custody status (stub for full cryptographic verification).",
                "securitySchemes": [{"type": "oauth2", "scopes": ["ds:read"]}],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                        "stream_key": {"type": "string"},
                        "seq": {"type": "integer"},
                        "ledger_id_h64": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
            {
                "name": "ds.verify_coord",
                "description": "Resolve and verify a coordinate (WX/ATT/EV) via backend decode.",
                "securitySchemes": [{"type": "oauth2", "scopes": ["ds:read"]}],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "coordinate": {"type": "string"},
                        "entity": {"type": "string"},
                        "session_id": {"type": "string"},
                    },
                    "required": ["coordinate"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "ds.auto_rate_coord",
                "description": "Apply a model-selected 0..3 rating + reason to a coordinate and return updated rollup.",
                "securitySchemes": [{"type": "oauth2", "scopes": ["ds:write"]}],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "coordinate": {"type": "string"},
                        "rating": {"type": "integer", "minimum": 0, "maximum": 3},
                        "reason": {"type": "string"},
                        "context_id": {"type": "string"},
                        "actor_id": {"type": "string"},
                        "actor_type": {"type": "string"},
                        "source": {"type": "string"},
                        "model": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "entity": {"type": "string"},
                        "session_id": {"type": "string"},
                    },
                    "required": ["coordinate", "rating"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "ds.introspect",
                "description": "Fetch backend runtime introspection signals for adaptive sequencing decisions.",
                "securitySchemes": [{"type": "oauth2", "scopes": ["ds:read"]}],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string"},
                        "session_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
        ]

    @staticmethod
    def _mcp_ok(*, call_id: Any, payload: dict[str, Any], text: str | None = None) -> dict[str, Any]:
        content_text = text or json.dumps(payload, separators=(",", ":"))
        return {
            "jsonrpc": "2.0",
            "id": call_id,
            "result": {
                "content": [{"type": "text", "text": content_text}],
                "structuredContent": payload,
                "isError": False,
            },
        }

    @staticmethod
    def _mcp_error(*, call_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": call_id,
            "error": {"code": code, "message": message},
        }

    async def _tool_handshake(self, args: dict[str, Any]) -> dict[str, Any]:
        peer_id = str(args.get("peer_id") or self.peer_id)
        alg_ids_raw = args.get("alg_ids")
        alg_ids = [1, 2]
        if isinstance(alg_ids_raw, list) and alg_ids_raw:
            alg_ids = [int(v) for v in alg_ids_raw]
        result = await self._post(
            "/sync/v0/handshake",
            {
                "peer_id": peer_id,
                "protocol_versions": [0],
                "envelope_versions": [0],
                "alg_ids": alg_ids,
            },
        )
        return result

    async def _tool_append_event(self, args: dict[str, Any]) -> dict[str, Any]:
        payload_obj = args.get("payload")
        if not isinstance(payload_obj, dict):
            raise RuntimeError("payload (object) is required")

        ledger_id_h64_arg = str(args.get("ledger_id_h64") or "").strip()
        if ledger_id_h64_arg:
            ledger_h64 = _normalize_h64(ledger_id_h64_arg)
        else:
            ledger_id = str(args.get("ledger_id") or self.default_ledger)
            ledger_h64 = _to_h64(ledger_id)

        subject = str(args.get("subject") or self.subject)
        issuer = str(args.get("issuer") or self.issuer)
        subject_h64 = _to_h64(subject)
        issuer_h64 = _to_h64(issuer)
        origin_repo_h64 = _to_h64(self.origin_repo)
        origin_node_h64 = _to_h64(self.origin_node)
        stream_key = f"{ledger_h64}:{subject_h64}:{issuer_h64}"

        state_seq, state_prev = self._get_stream_state(stream_key)
        seq = int(args.get("seq") or state_seq) & 0xFFFFFF
        prev_event_h64 = _normalize_h64(str(args.get("prev_event_h64") or state_prev))

        pipeline_result: dict[str, Any] | None = None
        event_payload: dict[str, Any] = dict(payload_obj)
        include_introspection = _as_bool(args.get("include_introspection"), default=True)
        auto_e6 = _as_bool(args.get("auto_e6"), default=self.auto_e6_enabled)
        session_id_for_introspection = str(
            args.get("session_id") or payload_obj.get("session_id") or f"mcp-{self.peer_id}"
        ).strip() or f"mcp-{self.peer_id}"
        entity_for_introspection = payload_obj.get("entity")
        if not isinstance(entity_for_introspection, str) or not entity_for_introspection.strip():
            entity_for_introspection = args.get("subject") or self.subject
        introspection_pre: dict[str, Any] | None = None
        introspection_post: dict[str, Any] | None = None
        if include_introspection:
            introspection_pre = await self._get_backend_introspection(
                entity=str(entity_for_introspection),
                session_id=session_id_for_introspection,
            )
        if self.append_pipeline_enabled and not bool(args.get("skip_pipeline", False)):
            pipeline_result = await self._run_turn_pipeline(args=args, payload_obj=payload_obj, ledger_h64=ledger_h64)
            if include_introspection:
                introspection_post = await self._get_backend_introspection(
                    entity=str(entity_for_introspection),
                    session_id=session_id_for_introspection,
                )
            assistant_reply = str(pipeline_result.get("reply") or "").strip()
            coordinate = str(pipeline_result.get("coordinate") or "").strip()
            stats = pipeline_result.get("stats") if isinstance(pipeline_result.get("stats"), dict) else {}
            event_payload = {
                "kind": "mcp_turn",
                "source": "mcp",
                "user_message": payload_obj.get("user_message") or payload_obj.get("message") or payload_obj.get("content") or payload_obj.get("msg") or "",
                "assistant_reply": assistant_reply,
                "coordinate": coordinate,
                "stats": stats,
                "raw_payload": payload_obj,
            }

        queue_depth = len(self._read_queue())
        header_fields = (
            self._derive_e6_header_fields(
                args=args,
                payload_obj=payload_obj,
                pipeline_result=pipeline_result,
                introspection_post=introspection_post,
                queue_depth=queue_depth,
            )
            if auto_e6
            else {
                "mode": 2,
                "ptype": 0,
                "law": 2,
                "route": 3,
                "node": 4,
                "K": 1,
                "P": 1,
                "E": 1,
                "valid": 1,
                "dW": 0,
                "V_q": 45000,
            }
        )
        event_payload["e6_header"] = dict(header_fields)
        payload_bytes = json.dumps(event_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        header = pack_header_v0(seq=seq, t_ms=0, **header_fields)
        trailer = {
            "alg_id": 1,
            "key_id": int(self.key_id),
            "ledger_id_h64": int(ledger_h64, 16),
            "origin_repo_h64": int(origin_repo_h64, 16),
            "origin_node_h64": int(origin_node_h64, 16),
            "subject_id_h64": int(subject_h64, 16),
            "issuer_id_h64": int(issuer_h64, 16),
            "nonce64": secrets.randbits(64),
            "prev_event_h64": int(prev_event_h64, 16),
            "payload_hash_h64": hash64(payload_bytes),
        }
        signing_input = build_signing_input(header, trailer)
        proof = self._load_private_key().sign(signing_input)
        envelope_hex = pack_envelope_v0(header, payload_bytes, trailer, proof).hex()

        allow_backfill = bool(args.get("allow_backfill", False))
        queue_on_failure = bool(args.get("queue_on_failure", True))
        request_payload = {
            "peer_id": self.peer_id,
            "ledger_id_h64": ledger_h64,
            "items": [{"envelope_hex": envelope_hex, "allow_backfill": allow_backfill}],
        }

        try:
            push = await self._post("/sync/v0/push", request_payload)
        except Exception as exc:
            if not queue_on_failure:
                raise RuntimeError(str(exc)) from exc
            self._enqueue(
                {
                    "queued_at": int(time.time()),
                    "ledger_id_h64": ledger_h64,
                    "stream_key": stream_key,
                    "seq": seq,
                    "prev_event_h64": prev_event_h64,
                    "request_payload": request_payload,
                }
            )
            return {
                "status": "queued",
                "reason": "backend_unavailable",
                "ledger_id_h64": ledger_h64,
                "stream_key": stream_key,
                "seq": seq,
                "queue_depth": len(self._read_queue()),
                "error": str(exc),
            }

        first = push.get("results", [{}])[0] if isinstance(push.get("results"), list) and push.get("results") else {}
        accepted_stream_key = stream_key
        accepted_seq = seq
        if isinstance(first, dict):
            backend_stream_key = first.get("stream_key")
            if isinstance(backend_stream_key, str) and backend_stream_key.strip():
                accepted_stream_key = backend_stream_key.strip()
            try:
                accepted_seq = int(first.get("seq") or accepted_seq)
            except Exception:
                accepted_seq = seq

        first_status = str(first.get("status") or "") if isinstance(first, dict) else ""
        if first_status == "accepted" and isinstance(first, dict) and first.get("event_id"):
            event_id = str(first.get("event_id"))
            self._set_stream_state(accepted_stream_key, next_seq=accepted_seq + 1, prev_event_h64=event_id)
            self._remember_event(
                event_id=event_id,
                stream_key=accepted_stream_key,
                seq=accepted_seq,
                ledger_id_h64=ledger_h64,
            )

        accepted_count = int(push.get("accepted") or 0)
        duplicate_count = int(push.get("duplicate") or 0)
        quarantine_count = int(push.get("quarantine") or 0)
        if quarantine_count > 0 or first_status == "quarantine":
            status = "quarantine"
        elif accepted_count > 0 or first_status == "accepted":
            status = "committed"
        elif duplicate_count > 0 or first_status == "duplicate":
            status = "duplicate"
        else:
            status = "queued"
            if queue_on_failure:
                self._enqueue(
                    {
                        "queued_at": int(time.time()),
                        "ledger_id_h64": ledger_h64,
                        "stream_key": stream_key,
                        "seq": seq,
                        "prev_event_h64": prev_event_h64,
                        "request_payload": request_payload,
                        "reason": "inconclusive_backend_result",
                    }
                )

        return {
            "status": status,
            "ledger_id_h64": ledger_h64,
            "stream_key": accepted_stream_key,
            "seq": accepted_seq,
            "e6_header": header_fields,
            "pipeline": pipeline_result,
            "introspection": {
                "entity": str(entity_for_introspection),
                "session_id": session_id_for_introspection,
                "pre": introspection_pre if isinstance(introspection_pre, dict) else {},
                "post": introspection_post if isinstance(introspection_post, dict) else {},
            }
            if include_introspection
            else None,
            "push": push,
        }

    async def _tool_sync_status(self, _args: dict[str, Any]) -> dict[str, Any]:
        backend = await self._get("/sync/v0/status")
        return {
            "backend": backend,
            "local_queue_depth": len(self._read_queue()),
        }

    async def _tool_sync_flush(self, args: dict[str, Any]) -> dict[str, Any]:
        ledger_id_h64_arg = str(args.get("ledger_id_h64") or "").strip()
        if ledger_id_h64_arg:
            ledger_h64 = _normalize_h64(ledger_id_h64_arg)
        else:
            ledger_id = str(args.get("ledger_id") or self.default_ledger)
            ledger_h64 = _to_h64(ledger_id)

        queued = self._read_queue()
        remaining: list[dict[str, Any]] = []
        accepted = 0
        duplicate = 0
        quarantine = 0

        for row in queued:
            try:
                request_payload = row.get("request_payload") if isinstance(row.get("request_payload"), dict) else None
                if not request_payload:
                    continue
                request_payload["ledger_id_h64"] = ledger_h64
                push = await self._post("/sync/v0/push", request_payload)
                accepted += int(push.get("accepted") or 0)
                duplicate += int(push.get("duplicate") or 0)
                quarantine += int(push.get("quarantine") or 0)
                results = push.get("results") if isinstance(push.get("results"), list) else []
                for result in results:
                    if not isinstance(result, dict):
                        continue
                    if str(result.get("status") or "") != "accepted":
                        continue
                    event_id = str(result.get("event_id") or "").strip().lower()
                    stream_key = str(result.get("stream_key") or "").strip()
                    if not event_id or not stream_key:
                        continue
                    try:
                        seq = int(result.get("seq"))
                    except Exception:
                        continue
                    self._remember_event(
                        event_id=event_id,
                        stream_key=stream_key,
                        seq=seq,
                        ledger_id_h64=ledger_h64,
                    )
            except Exception:
                remaining.append(row)

        self._write_queue(remaining)
        checkpoint = await self._post(
            "/sync/v0/checkpoint/save",
            {
                "peer_id": self.peer_id,
                "ledger_id_h64": ledger_h64,
                "cursor_name": "mcp_flush",
                "cursors": {},
                "metadata": {
                    "accepted": accepted,
                    "duplicate": duplicate,
                    "quarantine": quarantine,
                    "remaining_queue_depth": len(remaining),
                },
            },
        )
        return {
            "status": "ok",
            "ledger_id_h64": ledger_h64,
            "accepted": accepted,
            "duplicate": duplicate,
            "quarantine": quarantine,
            "remaining_queue_depth": len(remaining),
            "checkpoint": checkpoint,
        }

    async def _tool_verify(self, args: dict[str, Any]) -> dict[str, Any]:
        event_id = str(args.get("event_id") or "").strip().lower()
        stream_key = str(args.get("stream_key") or "").strip()
        seq_raw = args.get("seq")
        ledger_h64 = str(args.get("ledger_id_h64") or "").strip().lower()
        ledger_id = str(args.get("ledger_id") or self.default_ledger).strip()

        if not ledger_h64:
            if stream_key and ":" in stream_key:
                ledger_h64 = stream_key.split(":", 1)[0]
            else:
                ledger_h64 = _to_h64(ledger_id)
        ledger_h64 = _normalize_h64(ledger_h64)

        seq: int | None = None
        if isinstance(seq_raw, int):
            seq = int(seq_raw)
        else:
            try:
                seq = int(seq_raw) if seq_raw is not None else None
            except Exception:
                seq = None

        # Resolve by indexed event_id if stream/seq missing.
        if event_id and (not stream_key or seq is None):
            row = self._lookup_event_index(event_id)
            if row is None:
                row = await self._lookup_event_remote(ledger_h64=ledger_h64, event_id=event_id)
            if isinstance(row, dict):
                if not stream_key:
                    stream_key = str(row.get("stream_key") or "")
                if seq is None:
                    try:
                        seq = int(row.get("seq"))
                    except Exception:
                        seq = None
                if not ledger_h64:
                    ledger_h64 = str(row.get("ledger_id_h64") or ledger_h64)

        if not stream_key or seq is None:
            return {
                "status": "stub",
                "verified": False,
                "signature_valid": None,
                "chain_valid": None,
                "detail": "Provide stream_key+seq, or provide event_id for indexed/remote lookup.",
            }

        backfill = await self._post(
            "/sync/v0/backfill",
            {
                "peer_id": self.peer_id,
                "ledger_id_h64": ledger_h64,
                "stream_key": stream_key,
                "from_seq": int(seq),
                "to_seq": int(seq),
                "limit": 1,
            },
        )
        items = backfill.get("items") if isinstance(backfill.get("items"), list) else []
        item = items[0] if items else {}
        found_event_id = str(item.get("event_id") or "").lower() if isinstance(item, dict) else ""
        if not item:
            return {
                "status": "ok",
                "verified": False,
                "event_id": event_id or None,
                "stream_key": stream_key,
                "seq": int(seq),
                "detail": "No event found for requested stream/seq.",
            }

        envelope_hex = str(item.get("envelope_hex") or "")
        decoded = _decode_envelope_payload(envelope_hex)
        payload_text = str(decoded.get("payload_text") or "")
        payload_json = decoded.get("payload_json") if isinstance(decoded.get("payload_json"), dict) else None
        coord_candidates = _extract_coord_candidates(payload_text, payload_json)

        # Lightweight chain check using trailer prev_event_h64 and predecessor presence.
        chain_valid: bool | None = None
        prev_event_h64 = str(decoded.get("prev_event_h64") or "")
        if int(seq) <= 1:
            chain_valid = True
        elif prev_event_h64:
            prev = await self._post(
                "/sync/v0/backfill",
                {
                    "peer_id": self.peer_id,
                    "ledger_id_h64": ledger_h64,
                    "stream_key": stream_key,
                    "from_seq": int(seq) - 1,
                    "to_seq": int(seq) - 1,
                    "limit": 1,
                },
            )
            prev_items = prev.get("items") if isinstance(prev.get("items"), list) else []
            if prev_items and isinstance(prev_items[0], dict):
                prev_id = str(prev_items[0].get("event_id") or "").lower()
                chain_valid = prev_id == prev_event_h64.lower()
            else:
                chain_valid = False

        # Signature verification still pending until backend exposes cryptographic verify endpoint.
        signature_valid: bool | None = None

        self._remember_event(
            event_id=found_event_id,
            stream_key=stream_key,
            seq=int(seq),
            ledger_id_h64=ledger_h64,
        )

        return {
            "status": "ok",
            "verified": True,
            "event_id": found_event_id,
            "event_id_match": (found_event_id == event_id) if event_id else None,
            "ledger_id_h64": ledger_h64,
            "stream_key": stream_key,
            "seq": int(seq),
            "signature_valid": signature_valid,
            "chain_valid": chain_valid,
            "coords": coord_candidates,
            "payload": payload_json,
            "found": item if isinstance(item, dict) else {},
            "detail": "Presence verified; coords extracted from payload/envelope. Signature verify endpoint is TODO.",
        }

    async def _decode_coordinate_backend(
        self,
        coordinate: str,
        *,
        entity: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"coordinate": coordinate}
        if isinstance(entity, str) and entity.strip():
            payload["entity"] = entity.strip()
        if isinstance(session_id, str) and session_id.strip():
            payload["session_id"] = session_id.strip()

        failures: list[str] = []
        for path in ("/chat/web4/decode", "/web4/decode"):
            try:
                return await self._post(path, payload)
            except Exception as exc:
                failures.append(f"{path}: {exc}")
        raise RuntimeError("coordinate decode failed; " + " | ".join(failures))

    async def _tool_verify_coord(self, args: dict[str, Any]) -> dict[str, Any]:
        coordinate = str(args.get("coordinate") or "").strip()
        if not coordinate:
            raise RuntimeError("coordinate is required")

        entity = args.get("entity")
        session_id = args.get("session_id")
        decoded = await self._decode_coordinate_backend(
            coordinate,
            entity=entity if isinstance(entity, str) else None,
            session_id=session_id if isinstance(session_id, str) else None,
        )

        status = str(decoded.get("status") or "ok").lower()
        if status == "error":
            return {
                "status": "not_found",
                "verified": False,
                "coordinate": coordinate,
                "detail": str(decoded.get("detail") or decoded.get("error") or "coordinate unresolved"),
                "decoded": decoded,
            }

        resolved_coord = str(decoded.get("coordinate") or coordinate)
        has_content = bool(
            isinstance(decoded.get("content"), str)
            or isinstance(decoded.get("payload"), dict)
            or isinstance(decoded.get("metadata"), dict)
            or isinstance(decoded.get("skim"), dict)
            or isinstance(decoded.get("entry"), dict)
        )
        return {
            "status": "ok",
            "verified": bool(has_content or decoded),
            "coordinate": resolved_coord,
            "namespace": decoded.get("namespace"),
            "identifier": decoded.get("identifier"),
            "kind": decoded.get("kind"),
            "detail": "Coordinate resolved via backend decode.",
            "decoded": decoded,
        }

    async def _tool_auto_rate_coord(self, args: dict[str, Any]) -> dict[str, Any]:
        coordinate = str(args.get("coordinate") or "").strip()
        if not coordinate:
            raise RuntimeError("coordinate is required")

        rating_raw = args.get("rating")
        try:
            rating = int(rating_raw)
        except Exception as exc:
            raise RuntimeError("rating must be an integer in range 0..3") from exc
        if rating < 0 or rating > 3:
            raise RuntimeError("rating must be an integer in range 0..3")

        reason = args.get("reason")
        actor_id = args.get("actor_id")
        actor_type = args.get("actor_type")
        source = args.get("source")
        model = args.get("model")
        confidence = args.get("confidence")
        context_id_raw = args.get("context_id")
        context_id = str(context_id_raw or self.default_context_id).strip() or self.default_context_id

        entity = args.get("entity")
        session_id = args.get("session_id")
        decoded_before = await self._decode_coordinate_backend(
            coordinate,
            entity=entity if isinstance(entity, str) else None,
            session_id=session_id if isinstance(session_id, str) else None,
        )

        payload: dict[str, Any] = {
            "rating": rating,
            "context_id": context_id,
        }
        if isinstance(reason, str) and reason.strip():
            payload["reason"] = reason.strip()
        if isinstance(actor_id, str) and actor_id.strip():
            payload["actor_id"] = actor_id.strip()
        if isinstance(actor_type, str) and actor_type.strip():
            payload["actor_type"] = actor_type.strip()
        if isinstance(source, str) and source.strip():
            payload["source"] = source.strip()
        if isinstance(model, str) and model.strip():
            payload["model"] = model.strip()
        if isinstance(confidence, (int, float)):
            payload["confidence"] = float(confidence)

        response = await self._post(f"/ledger/feedback/auto/{coordinate}", payload)
        rollup = response.get("rollup") if isinstance(response.get("rollup"), dict) else {}
        return {
            "status": str(response.get("status") or "ok"),
            "coordinate": coordinate,
            "rating": rating,
            "rollup": rollup,
            "applied": response.get("applied"),
            "decoded": decoded_before,
            "detail": "Auto-rating applied and rollup updated.",
        }

    async def _tool_introspect(self, args: dict[str, Any]) -> dict[str, Any]:
        entity = args.get("entity")
        session_id = args.get("session_id")
        body = await self._get_backend_introspection(
            entity=entity if isinstance(entity, str) else None,
            session_id=session_id if isinstance(session_id, str) else None,
        )
        if not body:
            return {
                "status": "unavailable",
                "detail": "Backend introspection endpoint not available.",
                "data": {},
            }
        return {
            "status": "ok",
            "entity": entity if isinstance(entity, str) else None,
            "session_id": session_id if isinstance(session_id, str) else None,
            "data": body,
        }


    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "ds.handshake":
            return await self._tool_handshake(args)
        if name == "ds.append_event":
            return await self._tool_append_event(args)
        if name == "ds.sync_status":
            return await self._tool_sync_status(args)
        if name == "ds.sync_flush":
            return await self._tool_sync_flush(args)
        if name == "ds.verify":
            return await self._tool_verify(args)
        if name == "ds.verify_coord":
            return await self._tool_verify_coord(args)
        if name == "ds.auto_rate_coord":
            return await self._tool_auto_rate_coord(args)
        if name == "ds.introspect":
            return await self._tool_introspect(args)
        raise RuntimeError(f"unknown tool: {name}")

    async def handle_rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        call_id = payload.get("id")
        method = str(payload.get("method") or "")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": call_id,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "serverInfo": {"name": "ds-mcp", "version": "0.1.0"},
                        "capabilities": {"tools": {}},
                    },
                }

            if method == "notifications/initialized":
                return {"jsonrpc": "2.0", "id": call_id, "result": {}}

            if method == "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": call_id,
                    "result": {"tools": self._tool_defs()},
                }

            if method == "tools/call":
                name = str(params.get("name") or "")
                args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                result = await self.call_tool(name, args)
                return self._mcp_ok(call_id=call_id, payload=result)

            return self._mcp_error(call_id=call_id, code=-32601, message=f"Unsupported method: {method}")
        except Exception as exc:
            detail = str(exc).strip()
            if not detail:
                detail = f"{exc.__class__.__name__} (empty error message)"
            detail = f"{detail} | repr={exc!r}"
            return self._mcp_error(call_id=call_id, code=-32000, message=detail)
