#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

MIDDLEWARE_BASE = os.getenv("MCP_P0_MIDDLEWARE_BASE", "http://127.0.0.1:5001").rstrip("/")
BACKEND_BASE = os.getenv("MCP_P0_BACKEND_BASE", "http://127.0.0.1:8080").rstrip("/")
MCP_URL = os.getenv("MCP_P0_MCP_URL", f"{MIDDLEWARE_BASE}/mcp").rstrip("/")
TENANT_ID = os.getenv("MCP_P0_TENANT_ID", "demo-tenant")
LEDGER_ID = os.getenv("MCP_P0_LEDGER_ID", os.getenv("DEFAULT_LEDGER_ID", "default"))
LEDGER_ID_H64 = os.getenv("MCP_P0_LEDGER_ID_H64", "").strip().lower()
PEER_ID = os.getenv("MCP_P0_PEER_ID", os.getenv("MCP_SYNC_PEER_ID", "mcp-p0-harness"))
OFFLINE_MODE = os.getenv("MCP_P0_OFFLINE_MODE", "manual_stop_backend").strip().lower()


def _post_json(url: str, payload: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url=url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}


def _get_json(url: str, timeout: int = 10) -> dict[str, Any]:
    req = Request(url=url, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}


def _mcp_call(method: str, params: dict[str, Any], req_id: int) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    return _post_json(MCP_URL, payload)


def _tool_call(name: str, arguments: dict[str, Any], req_id: int) -> tuple[dict[str, Any], str]:
    resp = _mcp_call("tools/call", {"name": name, "arguments": arguments}, req_id)
    if "error" in resp:
        raise RuntimeError(f"tool error {name}: {resp['error']}")
    result = resp.get("result") if isinstance(resp.get("result"), dict) else {}
    structured = result.get("structuredContent") if isinstance(result.get("structuredContent"), dict) else {}
    content = result.get("content") if isinstance(result.get("content"), list) else []
    text = ""
    if content and isinstance(content[0], dict):
        text = str(content[0].get("text") or "")
    return structured, text


def _print_check(name: str, ok: bool, detail: str) -> None:
    status = "PASS" if ok else "FAIL"
    print(f"- {status} {name}: {detail}")


def _resolve_ledger_args() -> dict[str, Any]:
    if LEDGER_ID_H64:
        return {"ledger_id_h64": LEDGER_ID_H64}
    return {"ledger_id": LEDGER_ID}


def main() -> int:
    print("MCP P0 Harness")
    print(f"middleware={MIDDLEWARE_BASE}")
    print(f"backend={BACKEND_BASE}")
    print(f"mcp={MCP_URL}")

    failures = 0

    try:
        init = _mcp_call("initialize", {}, 1)
        ok_init = bool(isinstance(init.get("result"), dict) and init["result"].get("capabilities") is not None)
        _print_check("initialize", ok_init, f"result_keys={list((init.get('result') or {}).keys())}")
        if not ok_init:
            failures += 1

        tools = _mcp_call("tools/list", {}, 2)
        tool_list = tools.get("result", {}).get("tools", []) if isinstance(tools.get("result"), dict) else []
        names = [t.get("name") for t in tool_list if isinstance(t, dict)]
        required = {"ds.handshake", "ds.append_event", "ds.sync_status", "ds.sync_flush", "ds.verify"}
        ok_tools = required.issubset(set(str(n) for n in names))
        _print_check("tools_list", ok_tools, f"names={names}")
        if not ok_tools:
            failures += 1

        hs_args = {"peer_id": PEER_ID, "alg_ids": [1, 2]}
        hs, _ = _tool_call("ds.handshake", hs_args, 3)
        hs_ok = bool(hs.get("accepted") is True and 1 in (hs.get("alg_ids") or []))
        _print_check("handshake_ed25519", hs_ok, f"handshake={hs}")
        if not hs_ok:
            failures += 1

        append_payload = {
            "kind": "mcp_p0_demo",
            "tenant_id": TENANT_ID,
            "note": "append/verify baseline",
            "ts": int(time.time()),
        }
        append_args = {
            "payload": append_payload,
            "queue_on_failure": True,
            **_resolve_ledger_args(),
        }
        app1, _ = _tool_call("ds.append_event", append_args, 4)
        app1_status = str(app1.get("status") or "")
        app1_push = app1.get("push") if isinstance(app1.get("push"), dict) else {}
        app1_results = app1_push.get("results") if isinstance(app1_push.get("results"), list) else []
        app1_first = app1_results[0] if app1_results and isinstance(app1_results[0], dict) else {}
        event_id = str(app1_first.get("event_id") or "")
        stream_key = str(app1_first.get("stream_key") or app1.get("stream_key") or "")
        seq = int(app1_first.get("seq") or app1.get("seq") or 0)
        ok_append = app1_status == "committed" and bool(event_id) and bool(stream_key) and seq > 0
        _print_check("append_event_committed", ok_append, f"append={app1}")
        if not ok_append:
            failures += 1

        verify_args = {
            "event_id": event_id,
            "stream_key": stream_key,
            "seq": seq,
            **_resolve_ledger_args(),
        }
        ver, _ = _tool_call("ds.verify", verify_args, 5)
        ok_verify = bool(ver.get("verified") is True)
        _print_check("verify_presence", ok_verify, f"verify={ver}")
        if not ok_verify:
            failures += 1

        if OFFLINE_MODE == "manual_stop_backend":
            print("\\nOffline step: stop backend now for ~30 seconds, then press Enter to continue.")
            try:
                input()
            except EOFError:
                pass

        offline_payload = {
            "kind": "mcp_p0_demo",
            "tenant_id": TENANT_ID,
            "note": "offline queue test",
            "ts": int(time.time()),
        }
        app_offline, _ = _tool_call(
            "ds.append_event",
            {
                "payload": offline_payload,
                "queue_on_failure": True,
                **_resolve_ledger_args(),
            },
            6,
        )
        queued = str(app_offline.get("status") or "") == "queued"
        _print_check("offline_queue", queued, f"append_offline={app_offline}")
        if not queued:
            failures += 1

        if OFFLINE_MODE == "manual_stop_backend":
            print("Bring backend back online now, then press Enter to flush queue.")
            try:
                input()
            except EOFError:
                pass

        flush, _ = _tool_call("ds.sync_flush", _resolve_ledger_args(), 7)
        ok_flush = int(flush.get("accepted") or 0) >= 1 and int(flush.get("remaining_queue_depth") or 0) == 0
        _print_check("sync_flush", ok_flush, f"flush={flush}")
        if not ok_flush:
            failures += 1

        replay_payload = {
            "kind": "mcp_p0_demo",
            "tenant_id": TENANT_ID,
            "note": "forced divergence test",
            "ts": int(time.time()),
        }
        app_attack, _ = _tool_call(
            "ds.append_event",
            {
                "payload": replay_payload,
                "queue_on_failure": False,
                "seq": seq,
                "prev_event_h64": "0000000000000000",
                **_resolve_ledger_args(),
            },
            8,
        )
        attack_push = app_attack.get("push") if isinstance(app_attack.get("push"), dict) else {}
        attack_results = attack_push.get("results") if isinstance(attack_push.get("results"), list) else []
        attack_first = attack_results[0] if attack_results and isinstance(attack_results[0], dict) else {}
        reason = str(attack_first.get("reason") or "")
        status = str(attack_first.get("status") or "")
        ok_attack = status == "quarantine" and reason in {
            "divergence_seq_conflict",
            "chain_mismatch",
            "nonce_replay",
        }
        _print_check("replay_or_fork_quarantine", ok_attack, f"attack={app_attack}")
        if not ok_attack:
            failures += 1

        status_body = _get_json(f"{BACKEND_BASE}/sync/v0/status")
        _print_check("backend_status", True, f"status={status_body}")

    except HTTPError as exc:
        print(f"Harness HTTPError: {exc}")
        return 2
    except URLError as exc:
        print(f"Harness URLError: {exc}")
        return 2
    except Exception as exc:
        print(f"Harness Error: {exc}")
        return 2

    print(f"\\nSummary: failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
