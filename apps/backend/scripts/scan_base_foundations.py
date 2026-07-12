#!/usr/bin/env python3
"""Integrity scan: verify every provision/ledger has a base foundation.

Usage:
    DB_PATH=./data python scripts/scan_base_foundations.py

Exit code 0 = all provisioned ledgers have a foundation; 1 = any missing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.kernel.base_foundation import BaseFoundationService
from backend.services.pilot_provisioning import _load_jobs


def _discover_namespaces(db: Any) -> set[str]:
    namespaces: set[str] = set()
    try:
        keys = list(db.keys())
    except Exception:
        return namespaces
    for key in keys:
        text = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        if text.startswith("overlay:"):
            parts = text.split(":")
            if len(parts) >= 2:
                namespaces.add(parts[1])
    return namespaces


def _collect_ledger_ids_from_jobs(db: Any) -> set[str]:
    ledger_ids: set[str] = set()
    try:
        jobs = _load_jobs(db)
    except Exception:
        return ledger_ids
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        for step in job.get("resource_steps") or []:
            if not isinstance(step, dict):
                continue
            meta = step.get("metadata") or {}
            ledger_id = meta.get("ledger_id")
            if isinstance(ledger_id, str) and ledger_id.strip():
                ledger_ids.add(ledger_id.strip())
    return ledger_ids


def _collect_ledger_ids_from_registry(db: Any) -> set[str]:
    ledger_ids: set[str] = set()
    try:
        raw = db.get(b"__ledgers_v1__")
        payload = json.loads(raw.decode("utf-8")) if isinstance(raw, (bytes, bytearray)) else None
    except Exception:
        return ledger_ids
    if not isinstance(payload, dict):
        return ledger_ids
    rows = payload.get("ledgers", payload)
    if isinstance(rows, dict):
        for ledger_id in rows.keys():
            if isinstance(ledger_id, str) and ledger_id.strip():
                ledger_ids.add(ledger_id.strip())
    return ledger_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan base foundation coverage")
    parser.add_argument("--db-path", default=os.getenv("DB_PATH", "./data"), help="Path to RocksDB directory")
    args = parser.parse_args(argv)

    try:
        from rocksdict import Rdict
    except ImportError as exc:
        print(f"ERROR: rocksdict is required: {exc}", file=sys.stderr)
        return 1

    db_full = os.path.join(args.db_path, "ledger.db")
    if not os.path.isdir(os.path.dirname(db_full)):
        print(f"No database found at {os.path.dirname(db_full)}")
        return 0

    db = Rdict(db_full)
    try:
        targets = set()
        targets |= _collect_ledger_ids_from_jobs(db)
        targets |= _collect_ledger_ids_from_registry(db)
        targets |= _discover_namespaces(db)

        missing = []
        present = 0
        for ledger_id in sorted(targets):
            if BaseFoundationService(db).has_base_foundation(ledger_id):
                present += 1
            else:
                missing.append(ledger_id)

        print(f"Scanned {len(targets)} ledgers: present={present} missing={len(missing)}")
        for ledger_id in missing:
            print(f"MISSING foundation: {ledger_id}", file=sys.stderr)
        return 0 if not missing else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
