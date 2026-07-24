#!/usr/bin/env python3
"""Epic 11 ship gate for Telegram and Document surfaces.

Usage:
    python eval/epic11_ship_gate.py \
        --backend https://dss-system-backend.fly.dev \
        --principal-did did:web:example \
        --ledger-id pilot \
        --telegram-admin-secret $TELEGRAM_ADMIN_SECRET

Outputs:
    - eval/reports/epic11_ship_gate_results.json
    - Console pass/fail summary
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "eval" / "reports" / "epic11_ship_gate_results.json"


def _session_token_for_principal(
    backend: str,
    principal_did: str,
    ledger_id: str,
) -> str:
    """Mint a surface session token for the gate principal."""
    url = f"{backend.rstrip('/')}/auth/token"
    payload = {
        "principal_did": principal_did,
        "auth_method": "passkey",
        "ledger_ids": [ledger_id],
        "ttl_seconds": 600,
    }
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return str(data["session"]["token"])


def _doc_headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}", "content-type": "application/json"}


def _run_document_tests(backend: str, token: str, ledger_id: str) -> dict[str, Any]:
    """Exercise the document surface API and verify event-sourcing guarantees."""
    base = backend.rstrip("/")
    results: dict[str, Any] = {"surface": "document", "checks": []}
    doc_id: str | None = None
    chunk_coord: str | None = None

    def record(name: str, passed: bool, detail: str = "") -> None:
        results["checks"].append({"name": name, "passed": passed, "detail": detail})

    try:
        # Create document
        resp = httpx.post(
            f"{base}/v1/documents",
            headers=_doc_headers(token),
            json={"title": "Ship gate doc"},
            timeout=30,
        )
        resp.raise_for_status()
        doc_id = resp.json()["doc_id"]
        record("create_document", True, f"doc_id={doc_id}")
    except Exception as exc:
        record("create_document", False, str(exc))
        results["passed"] = False
        return results

    try:
        # Create chunk
        resp = httpx.post(
            f"{base}/v1/documents/{doc_id}/chunks",
            headers=_doc_headers(token),
            json={"prompt": "Write a concise ship gate summary."},
            timeout=120,
        )
        resp.raise_for_status()
        chunk = resp.json()
        chunk_coord = chunk["chunk_coord"]
        version_coord = chunk["version_coord"]
        record("create_chunk", True, f"chunk={chunk_coord}, version={version_coord}")
    except Exception as exc:
        record("create_chunk", False, str(exc))
        results["passed"] = False
        return results

    try:
        # Reprompt appends a new version; prior version coord unchanged.
        resp = httpx.post(
            f"{base}/v1/documents/chunks/{chunk_coord}/reprompt",
            headers=_doc_headers(token),
            json={"prompt": "Rewrite the summary in one sentence."},
            timeout=120,
        )
        resp.raise_for_status()
        reprompt = resp.json()
        new_version = reprompt["version_coord"]
        versions_resp = httpx.get(
            f"{base}/v1/documents/chunks/{chunk_coord}/versions",
            headers=_doc_headers(token),
            timeout=30,
        )
        versions_resp.raise_for_status()
        versions = versions_resp.json().get("versions", [])
        record(
            "reprompt_appends_version",
            version_coord in versions and new_version in versions and len(versions) >= 2,
            f"versions={versions}",
        )
    except Exception as exc:
        record("reprompt_appends_version", False, str(exc))

    try:
        # Reorder emits a meta event; chunk coord remains stable.
        resp = httpx.patch(
            f"{base}/v1/documents/chunks/{chunk_coord}",
            headers=_doc_headers(token),
            json={"position": 5},
            timeout=30,
        )
        resp.raise_for_status()
        doc = httpx.get(
            f"{base}/v1/documents/{doc_id}",
            headers=_doc_headers(token),
            timeout=30,
        ).json()
        chunk_after = next((c for c in doc.get("chunks", []) if c["chunk_coord"] == chunk_coord), {})
        record(
            "reorder_is_event_sourced",
            chunk_after.get("position") == 5 and chunk_after.get("chunk_coord") == chunk_coord,
            f"position={chunk_after.get('position')}",
        )
    except Exception as exc:
        record("reorder_is_event_sourced", False, str(exc))

    try:
        # Selection bounds are refused, not clamped.
        resp = httpx.patch(
            f"{base}/v1/documents/chunks/{chunk_coord}",
            headers=_doc_headers(token),
            json={"sel_start": 0, "sel_end": 999999},
            timeout=30,
        )
        record("selection_bounds_refused", resp.status_code == 400, f"status={resp.status_code}")
    except Exception as exc:
        record("selection_bounds_refused", False, str(exc))

    try:
        # Delete sets visible:false; export then omits the chunk.
        before_export = httpx.get(
            f"{base}/v1/documents/{doc_id}/export",
            headers=_doc_headers(token),
            timeout=30,
        ).json()
        httpx.patch(
            f"{base}/v1/documents/chunks/{chunk_coord}",
            headers=_doc_headers(token),
            json={"visible": False},
            timeout=30,
        ).raise_for_status()
        after_export = httpx.get(
            f"{base}/v1/documents/{doc_id}/export",
            headers=_doc_headers(token),
            timeout=30,
        ).json()
        record(
            "delete_sets_visible_false",
            bool(before_export.get("text")) and not after_export.get("text"),
            f"before_len={len(before_export.get('text', ''))} after_len={len(after_export.get('text', ''))}",
        )
    except Exception as exc:
        record("delete_sets_visible_false", False, str(exc))

    try:
        # Export determinism: two reads after identical state are byte-identical.
        e1 = httpx.get(
            f"{base}/v1/documents/{doc_id}/export",
            headers=_doc_headers(token),
            timeout=30,
        ).json()
        e2 = httpx.get(
            f"{base}/v1/documents/{doc_id}/export",
            headers=_doc_headers(token),
            timeout=30,
        ).json()
        record(
            "export_is_deterministic",
            e1.get("text") == e2.get("text") and e1.get("title") == e2.get("title"),
            f"text_len={len(e1.get('text', ''))}",
        )
    except Exception as exc:
        record("export_is_deterministic", False, str(exc))

    results["passed"] = all(c["passed"] for c in results["checks"])
    return results


def _run_telegram_tests(
    backend: str,
    principal_did: str,
    admin_secret: str,
) -> dict[str, Any]:
    """Exercise Telegram pairing and synthetic webhook without a real Bot API token."""
    base = backend.rstrip("/")
    results: dict[str, Any] = {"surface": "telegram", "checks": []}

    def record(name: str, passed: bool, detail: str = "") -> None:
        results["checks"].append({"name": name, "passed": passed, "detail": detail})

    try:
        resp = httpx.post(
            f"{base}/v1/telegram/pairing-code",
            headers={"x-telegram-admin-secret": admin_secret, "content-type": "application/json"},
            json={"principal_did": principal_did},
            timeout=30,
        )
        resp.raise_for_status()
        code = resp.json()["code"]
        record("mint_pairing_code", True, f"code={code}")
    except Exception as exc:
        record("mint_pairing_code", False, str(exc))
        results["passed"] = False
        return results

    try:
        update_id = int(time.time() * 1000)
        resp = httpx.post(
            f"{base}/v1/telegram/webhook",
            json={
                "update_id": update_id,
                "message": {
                    "message_id": update_id,
                    "date": int(time.time()),
                    "chat": {"id": 12345, "type": "private"},
                    "text": f"/start {code}",
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        record("pairing_webhook_accepts", resp.json().get("ok") is True, f"status={resp.status_code}")
    except Exception as exc:
        record("pairing_webhook_accepts", False, str(exc))

    try:
        # A second message exercises the bound-principal path. Without a real bot
        # token the downstream sendMessage is skipped, but the webhook still returns
        # ok and surfaces abstention/refusal verbatim if chat returns an error.
        resp = httpx.post(
            f"{base}/v1/telegram/webhook",
            json={
                "update_id": update_id + 1,
                "message": {
                    "message_id": update_id + 1,
                    "date": int(time.time()),
                    "chat": {"id": 12345, "type": "private"},
                    "text": "What is the ship gate status?",
                },
            },
            timeout=120,
        )
        resp.raise_for_status()
        record("inbound_message_webhook_accepts", resp.json().get("ok") is True, f"status={resp.status_code}")
    except Exception as exc:
        record("inbound_message_webhook_accepts", False, str(exc))

    results["passed"] = all(c["passed"] for c in results["checks"])
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Epic 11 ship gate")
    parser.add_argument("--backend", default=os.getenv("DUALSUBSTRATE_API", "http://localhost:8000"))
    parser.add_argument("--principal-did", default=os.getenv("EPIC11_PRINCIPAL_DID", ""))
    parser.add_argument("--ledger-id", default=os.getenv("EPIC11_LEDGER_ID", "pilot"))
    parser.add_argument("--telegram-admin-secret", default=os.getenv("TELEGRAM_ADMIN_SECRET", ""))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.principal_did:
        print("ERROR: --principal-did is required (or set EPIC11_PRINCIPAL_DID)")
        return 2

    summary: dict[str, Any] = {
        "epic": "BACKLOG-EPIC-11",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "backend": args.backend.rstrip("/"),
        "principal_did": args.principal_did,
        "ledger_id": args.ledger_id,
        "surfaces": [],
    }

    try:
        token = _session_token_for_principal(args.backend, args.principal_did, args.ledger_id)
    except Exception as exc:
        print(f"ERROR: could not mint session token: {exc}")
        summary["error"] = f"session_token_failed: {exc}"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2))
        return 1

    doc_results = _run_document_tests(args.backend, token, args.ledger_id)
    summary["surfaces"].append(doc_results)

    if args.telegram_admin_secret:
        tg_results = _run_telegram_tests(args.backend, args.principal_did, args.telegram_admin_secret)
        summary["surfaces"].append(tg_results)
    else:
        summary["surfaces"].append({
            "surface": "telegram",
            "skipped": True,
            "reason": "TELEGRAM_ADMIN_SECRET not provided",
        })

    overall_pass = all(s.get("passed") for s in summary["surfaces"] if "passed" in s)
    summary["overall_passed"] = overall_pass

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2))

    print(f"Epic 11 ship gate: {'PASS' if overall_pass else 'FAIL'}")
    for surface in summary["surfaces"]:
        name = surface.get("surface", "unknown")
        if surface.get("skipped"):
            print(f"  {name}: SKIPPED ({surface.get('reason')})")
            continue
        status = "PASS" if surface.get("passed") else "FAIL"
        print(f"  {name}: {status}")
        for check in surface.get("checks", []):
            cstatus = "PASS" if check.get("passed") else "FAIL"
            print(f"    - {check['name']}: {cstatus}")
    print(f"Report written to {args.output}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
