#!/usr/bin/env python3
"""Backfill missing base ledger foundations for existing provisions and ledgers.

Usage:
    DB_PATH=./data python scripts/backfill_base_foundations.py [--dry-run]

The script:
  1. Iterates pilot provisioning jobs and writes a foundation for each ledger_id.
  2. Iterates the ledger registry v1 and writes a foundation for each ledger.
  3. Optionally discovers namespaces from overlay keys and backfills them too.
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
from backend.services.ledger_service import LedgerService
from backend.services.pilot_provisioning import _load_jobs


def _discover_namespaces(db: Any) -> set[str]:
    """Discover ledger namespaces from overlay keys."""
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
    """Extract ledger_ids from pilot provisioning jobs."""
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
    """Extract ledger_ids from the v1 ledger registry."""
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
    parser = argparse.ArgumentParser(description="Backfill base ledger foundations")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without writing")
    parser.add_argument("--db-path", default=os.getenv("DB_PATH", "./data"), help="Path to RocksDB directory")
    args = parser.parse_args(argv)

    try:
        from rocksdict import Rdict
    except ImportError as exc:
        print(f"ERROR: rocksdict is required: {exc}", file=sys.stderr)
        return 1

    db_full = os.path.join(args.db_path, "ledger.db")
    if not os.path.isdir(os.path.dirname(db_full)):
        os.makedirs(os.path.dirname(db_full), exist_ok=True)

    db = Rdict(db_full)
    try:
        service = LedgerService(db)

        targets = set()
        targets |= _collect_ledger_ids_from_jobs(db)
        targets |= _collect_ledger_ids_from_registry(db)
        targets |= _discover_namespaces(db)

        written = 0
        skipped = 0
        failed = 0
        for ledger_id in sorted(targets):
            try:
                has_foundation = BaseFoundationService(db).has_base_foundation(ledger_id)
                if has_foundation:
                    skipped += 1
                    continue
                if args.dry_run:
                    print(f"DRY-RUN: would write foundation for {ledger_id}")
                    written += 1
                    continue
                service.ensure_base_foundation(ledger_id)
                written += 1
                print(f"Wrote foundation for {ledger_id}")
            except Exception as exc:
                failed += 1
                print(f"FAILED {ledger_id}: {exc}", file=sys.stderr)

        print(
            f"Done. targets={len(targets)} written={written} skipped={skipped} failed={failed}"
        )
        return 0 if failed == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
